"""Experts API(s18):内置 + 用户专家的 CRUD(可信 harness 边界)。

- GET    /api/v1/experts                 — 列表(含内置,内置在前)
- POST   /api/v1/experts                 — 新建(user 层)
- PATCH  /api/v1/experts/{id}            — 更新(内置更新 = user 层覆盖)
- DELETE /api/v1/experts/{id}            — 删除(仅 user 层;内置报 400)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_runtime, require_auth

router = APIRouter(prefix="/api/v1/experts", tags=["experts"])


@router.get("", dependencies=[Depends(require_auth)])
async def list_experts(runtime=Depends(get_runtime)):
    return {"experts": [e.to_api() for e in runtime.experts.list()]}


@router.post("", dependencies=[Depends(require_auth)])
async def create_expert(body: dict, runtime=Depends(get_runtime)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name must not be empty")
    if len(name) > 60:
        raise HTTPException(status_code=400, detail="name too long (max 60)")
    exp = runtime.experts.create(
        name=name,
        role=(body.get("role") or "").strip(),
        system_prompt=body.get("systemPrompt") or "",
        color=body.get("color") or "#7c3aed",
        kb_ids=[str(k) for k in body.get("kbIds", [])],
        enabled=bool(body.get("enabled", True)),
    )
    return exp.to_api()


@router.patch("/{expert_id}", dependencies=[Depends(require_auth)])
async def update_expert(expert_id: str, body: dict, runtime=Depends(get_runtime)):
    fields: dict = {}
    if "name" in body:
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name must not be empty")
        fields["name"] = name
    for key in ("role", "systemPrompt", "color"):
        if key in body:
            fields["role" if key == "role" else
                   ("system_prompt" if key == "systemPrompt" else key)] = body[key]
    if "enabled" in body:
        fields["enabled"] = bool(body["enabled"])
    if "kbIds" in body:
        fields["kb_ids"] = [str(k) for k in body["kbIds"]]
    if not fields:
        raise HTTPException(status_code=400, detail="no updatable field provided")
    exp = runtime.experts.update(expert_id, **fields)
    if exp is None:
        raise HTTPException(status_code=404, detail="expert not found")
    return exp.to_api()


@router.delete("/{expert_id}", dependencies=[Depends(require_auth)])
async def delete_expert(expert_id: str, runtime=Depends(get_runtime)):
    ok, reason = runtime.experts.delete(expert_id)
    if not ok:
        status = 404 if reason == "expert not found" else 400
        raise HTTPException(status_code=status, detail=reason)
    return {"status": "deleted", "expert_id": expert_id, "detail": reason}
