"""Knowledge base API(知识库):库/文档 CRUD + multipart 上传 + 检索预览。

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

import json
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..deps import get_runtime, require_auth
from ...config import KB_ALLOWED_EXTS, KB_UPLOAD_LIMIT_MB, KB_UPLOADS_DIR
from ...knowledge import KBStore

router = APIRouter(prefix="/api/v1/kb", tags=["kb"])


def _store(runtime) -> KBStore:
    store = getattr(runtime, "kb_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="知识库子系统不可用")
    return store


def _require_kb(store: KBStore, kb_id: str) -> dict:
    kb = store.get_kb(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
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
        raise HTTPException(status_code=503, detail="知识库子系统不可用")
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


# --- offline eval (RAG 评估:后台线程,同 ingest worker 模式) ----------------------
#
# POST /api/v1/kb/{kb_id}/eval/run        — 启动一次离线评估(202,后台跑)
# GET  /api/v1/kb/eval/reports?kb_id=     — 报告列表(新到旧)
# GET  /api/v1/kb/eval/reports/latest?kb_id= — 最新报告(含运行状态)

_eval_jobs: dict[str, dict] = {}   # kb_id -> {"status","started_at","error","report_path"}


def _eval_job(kb_id: str) -> dict:
    return _eval_jobs.setdefault(kb_id, {"status": "idle"})


def _run_eval_job(kb_id: str, runtime, top_k: int, judge: str | None,
                  limit: int | None, with_ragas: bool) -> None:
    """线程体:组装评估链路并落报告;任何失败都记进 job 状态,不拖垮 sidecar。"""
    import logging
    from ...knowledge import eval_harness
    from ...knowledge.eval_harness import EvalCancelled
    log = logging.getLogger("soul_buddy.kb")
    job = _eval_job(kb_id)

    def _progress(stage: str, done: int, total: int) -> None:
        # 进度双写:job 状态给 UI 轮询,sidecar.log 留痕可回溯
        job.update(stage=stage, done=done, total=total)
        log.info("eval progress kb=%s stage=%s %d/%d", kb_id, stage, done, total)

    def _cancelled() -> bool:
        return bool(job.get("cancel_requested"))

    try:
        from ...config import Settings
        settings = Settings.load()
        store = _store(runtime)
        retriever = getattr(runtime, "kb_retriever", None)
        items = store_evalset_items(kb_id)
        if limit:
            items = items[:limit]
        rt_settings = getattr(runtime, "settings", None)
        sp = getattr(rt_settings, "provider", None) if rt_settings else None
        log.info("eval started kb=%s n=%d top_k=%d ragas=%s",
                 kb_id, len(items), top_k, with_ragas)
        report = eval_harness.run_eval(
            kb_id, retriever, items, top_k=top_k,
            provider=runtime.provider, with_ragas=with_ragas,
            judge_override=judge, settings=settings,
            session_provider=sp, progress=_progress, cancel_check=_cancelled)
        job.update(status="done", report_path=report["meta"]["report_path"],
                   error=None)
        log.info("eval done kb=%s report=%s", kb_id, job["report_path"])
    except EvalCancelled:
        job.update(status="cancelled", error=None)
        log.info("eval cancelled kb=%s", kb_id)
    except Exception as exc:
        log.exception("eval job failed")
        job.update(status="error", error=str(exc)[:400])


def store_evalset_items(kb_id: str) -> list[dict]:
    from ...knowledge.evalset import load_evalset
    return load_evalset(kb_id)["items"]


@router.get("/{kb_id}/eval/set", dependencies=[Depends(require_auth)])
async def eval_set_info(kb_id: str, runtime=Depends(get_runtime)):
    """评测集概要(是否存在/题数/内容指纹),供前端决定评估页的可用状态。"""
    _require_kb(_store(runtime), kb_id)
    try:
        items = store_evalset_items(kb_id)
    except FileNotFoundError:
        return {"exists": False, "count": 0, "hash": None}
    from ...knowledge.evalset import evalset_hash
    return {"exists": True, "count": len(items),
            "hash": evalset_hash({"items": items})}


@router.post("/{kb_id}/eval/set/items", dependencies=[Depends(require_auth)])
async def eval_set_append(kb_id: str, body: dict, runtime=Depends(get_runtime)):
    """追加评测题(在线零命中查询一键入库的通道)。"""
    _require_kb(_store(runtime), kb_id)
    from ...knowledge.evalset import append_items, new_item
    try:
        item = new_item(
            str(body.get("q") or ""), type=str(body.get("type") or "factoid"),
            gold_chunks=body.get("gold_chunks") or [],
            gold_keywords=body.get("gold_keywords") or [],
            reference=str(body.get("reference") or ""),
            must_refuse=bool(body.get("must_refuse")),
            source=str(body.get("source") or "online"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    saved = append_items(kb_id, [item])
    return {"status": "added" if saved.get("added") else "duplicate",
            "count": len(saved["items"])}


@router.get("/eval/online", dependencies=[Depends(require_auth)])
async def eval_online(kb_id: str | None = None, runtime=Depends(get_runtime)):
    """在线回流聚合:总量/零命中榜/块被引用率/死块/最近事件。"""
    if getattr(runtime, "kb_store", None) is None:
        raise HTTPException(status_code=503, detail="知识库子系统不可用")
    from ...knowledge.online import online_summary
    return online_summary(runtime.kb_store, kb_id)


@router.post("/{kb_id}/eval/run", dependencies=[Depends(require_auth)])
async def start_eval(kb_id: str, body: dict, runtime=Depends(get_runtime)):
    store = _store(runtime)
    _require_kb(store, kb_id)
    retriever = getattr(runtime, "kb_retriever", None)
    if retriever is None or not retriever.available():
        raise HTTPException(
            status_code=503,
            detail="检索链路不可用(embedding 未配置),无法评估")
    try:
        n_items = len(store_evalset_items(kb_id))
    except FileNotFoundError:
        raise HTTPException(
            status_code=400,
            detail=f"评测集不存在,先用 CLI gen 造题: evals/{kb_id}.json")
    if n_items == 0:
        raise HTTPException(status_code=400, detail="评测集为空")
    job = _eval_job(kb_id)
    if job.get("status") == "running":
        raise HTTPException(status_code=409, detail="该知识库已有评估在运行")
    job.update(status="running", started_at=time.time(), error=None,
               cancel_requested=False, stage=None, done=0, total=0)
    import threading
    threading.Thread(
        target=_run_eval_job,
        args=(kb_id, runtime, int(body.get("top_k") or 5),
              body.get("judge"), body.get("limit"),
              bool(body.get("with_ragas", True))),
        daemon=True, name=f"kb-eval-{kb_id}").start()
    return {"status": "started", "kb_id": kb_id, "n_items": n_items}


@router.post("/{kb_id}/eval/cancel", dependencies=[Depends(require_auth)])
async def cancel_eval(kb_id: str, runtime=Depends(get_runtime)):
    """请求取消运行中的评估:线程在下一题检查点自行退出(非抢占)。"""
    _require_kb(_store(runtime), kb_id)
    job = _eval_job(kb_id)
    if job.get("status") != "running":
        raise HTTPException(status_code=409, detail="没有正在运行的评估")
    job["cancel_requested"] = True
    return {"status": "cancelling", "kb_id": kb_id}


@router.get("/eval/status/{kb_id}", dependencies=[Depends(require_auth)])
async def eval_status(kb_id: str, runtime=Depends(get_runtime)):
    _require_kb(_store(runtime), kb_id)
    return {"kb_id": kb_id, **_eval_job(kb_id)}


@router.get("/eval/reports", dependencies=[Depends(require_auth)])
async def eval_reports(kb_id: str, runtime=Depends(get_runtime)):
    _require_kb(_store(runtime), kb_id)
    from ...knowledge import eval_harness
    return {"reports": eval_harness.list_reports(kb_id)}


@router.get("/eval/reports/latest", dependencies=[Depends(require_auth)])
async def eval_report_latest(kb_id: str, runtime=Depends(get_runtime)):
    _require_kb(_store(runtime), kb_id)
    from ...knowledge import eval_harness
    job = _eval_job(kb_id)
    reports = eval_harness.list_reports(kb_id)
    if not reports:
        return {"report": None, "job": {k: v for k, v in job.items()}}
    path = reports[0]["path"]
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    return {"report": report, "job": {k: v for k, v in job.items()}}
