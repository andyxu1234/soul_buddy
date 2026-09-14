"""Knowledge base API(资料库):库/文档 CRUD + multipart 上传 + 检索预览。

- GET    /api/v1/kb                                    — 库列表(含文档计数)
- POST   /api/v1/kb                                    — 新建库
- PATCH  /api/v1/kb/{kb_id}                            — 改名/描述
- DELETE /api/v1/kb/{kb_id}                            — 删库(向量+原文联动清理)
- GET    /api/v1/kb/{kb_id}/documents                  — 文档列表(含 ingest 状态)
- POST   /api/v1/kb/{kb_id}/documents                  — multipart 上传,入队索引
- DELETE /api/v1/kb/{kb_id}/documents/{doc_id}         — 删文档(向量+原文联动清理)
- POST   /api/v1/kb/{kb_id}/documents/{doc_id}/reindex — 重新索引
- POST   /api/v1/kb/search                             — 检索预览(调试/前端搜索框)
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..deps import get_runtime, require_auth
from ...config import KB_ALLOWED_EXTS, KB_UPLOAD_LIMIT_MB, KB_UPLOADS_DIR
from ...knowledge import KBStore

router = APIRouter(prefix="/api/v1/kb", tags=["kb"])


def _store(runtime) -> KBStore:
    store = getattr(runtime, "kb_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="资料库子系统不可用")
    return store


def _require_kb(store: KBStore, kb_id: str) -> dict:
    kb = store.get_kb(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="资料库不存在")
    return kb


# --- knowledge bases ---------------------------------------------------------

@router.get("", dependencies=[Depends(require_auth)])
async def list_kbs(runtime=Depends(get_runtime)):
    return {"kbs": _store(runtime).list_kbs()}


@router.post("", dependencies=[Depends(require_auth)])
async def create_kb(body: dict, runtime=Depends(get_runtime)):
    try:
        return _store(runtime).create_kb(body.get("name") or "",
                                         body.get("description") or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/{kb_id}", dependencies=[Depends(require_auth)])
async def update_kb(kb_id: str, body: dict, runtime=Depends(get_runtime)):
    store = _store(runtime)
    _require_kb(store, kb_id)
    kb = store.update_kb(kb_id, name=body.get("name"),
                         description=body.get("description"))
    return kb


@router.delete("/{kb_id}", dependencies=[Depends(require_auth)])
async def delete_kb(kb_id: str, runtime=Depends(get_runtime)):
    store = _store(runtime)
    _require_kb(store, kb_id)
    try:
        ok = store.delete_kb(kb_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if ok:
        vectors = getattr(runtime, "kb_vectors", None)
        if vectors is not None:
            try:
                vectors.drop_collection(kb_id)
            except Exception:
                pass   # 向量层失败不阻塞元数据删除(重建索引可恢复)
        import shutil
        shutil.rmtree(Path(KB_UPLOADS_DIR) / kb_id, ignore_errors=True)
    return {"status": "deleted", "kb_id": kb_id}


# --- documents ---------------------------------------------------------------

@router.get("/{kb_id}/documents", dependencies=[Depends(require_auth)])
async def list_documents(kb_id: str, runtime=Depends(get_runtime)):
    store = _store(runtime)
    _require_kb(store, kb_id)
    return {"documents": store.list_documents(kb_id)}


@router.post("/{kb_id}/documents", dependencies=[Depends(require_auth)])
async def upload_documents(kb_id: str, files: list[UploadFile] = File(...),
                           runtime=Depends(get_runtime)):
    """multipart 上传:原文落 <uploads>/<kb_id>/<doc_id><ext>,入队后台索引。"""
    store = _store(runtime)
    _require_kb(store, kb_id)
    if not files:
        raise HTTPException(status_code=400, detail="没有收到文件")
    limit = KB_UPLOAD_LIMIT_MB * 1024 * 1024
    dest_dir = Path(KB_UPLOADS_DIR) / kb_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    created, rejected = [], []
    for f in files:
        filename = Path(f.filename or "unnamed").name
        ext = Path(filename).suffix.lower()
        if ext not in KB_ALLOWED_EXTS:
            rejected.append({"filename": filename,
                             "reason": f"不支持的类型 {ext or '(无扩展名)'}"})
            continue
        data = await f.read()
        if len(data) > limit:
            rejected.append({"filename": filename,
                             "reason": f"超过 {KB_UPLOAD_LIMIT_MB}MB 上限"})
            continue
        if not data:
            rejected.append({"filename": filename, "reason": "空文件"})
            continue
        from ...models import new_id
        doc_id = new_id()
        dest = dest_dir / f"{doc_id}{ext}"
        dest.write_bytes(data)
        doc = store.add_document(kb_id, filename=filename, ext=ext,
                                 size_bytes=len(data), source_path=str(dest),
                                 doc_id=doc_id)
        worker = getattr(runtime, "kb_ingest", None)
        if worker is not None:
            worker.enqueue(doc_id)
        else:
            store.set_status(doc_id, "failed", error="索引线程不可用")
        created.append(doc)
    return {"documents": created, "rejected": rejected}


@router.delete("/{kb_id}/documents/{doc_id}", dependencies=[Depends(require_auth)])
async def delete_document(kb_id: str, doc_id: str, runtime=Depends(get_runtime)):
    store = _store(runtime)
    _require_kb(store, kb_id)
    doc = store.delete_document(doc_id)
    if doc is None or doc["kb_id"] != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    vectors = getattr(runtime, "kb_vectors", None)
    if vectors is not None:
        try:
            vectors.delete_doc(kb_id, doc_id)
        except Exception:
            pass
    try:
        Path(doc["source_path"]).unlink(missing_ok=True)
    except Exception:
        pass
    return {"status": "deleted", "doc_id": doc_id}


@router.post("/{kb_id}/documents/{doc_id}/reindex",
             dependencies=[Depends(require_auth)])
async def reindex_document(kb_id: str, doc_id: str, runtime=Depends(get_runtime)):
    store = _store(runtime)
    _require_kb(store, kb_id)
    doc = store.get_document(doc_id)
    if doc is None or doc["kb_id"] != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    if not Path(doc["source_path"] or "").exists():
        raise HTTPException(status_code=409, detail="原文文件已丢失，请删除后重新上传")
    store.set_status(doc_id, "pending", error=None)
    worker = getattr(runtime, "kb_ingest", None)
    if worker is not None:
        worker.enqueue(doc_id)
    return store.get_document(doc_id)


# --- search preview ------------------------------------------------------------

@router.post("/search", dependencies=[Depends(require_auth)])
async def search(body: dict, runtime=Depends(get_runtime)):
    retriever = getattr(runtime, "kb_retriever", None)
    if retriever is None:
        raise HTTPException(status_code=503, detail="资料库子系统不可用")
    from ...knowledge import RetrievalUnavailable
    store = _store(runtime)
    kb_ids = [str(k) for k in body.get("kb_ids", [])] \
        or [kb["id"] for kb in store.list_kbs()]
    top_k = int(body.get("top_k") or 5)
    try:
        results = retriever.search(body.get("query") or "", kb_ids,
                                   top_k=max(1, min(top_k, 20)))
    except RetrievalUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"results": results, "count": len(results)}
