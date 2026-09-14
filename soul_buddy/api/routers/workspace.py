"""Workspace file-tree API.

- GET /api/v1/workspace/tree?root=<path>&depth=<n>&search=<q>
  列出工作区文件树。支持按深度限制、按名称搜索。
  返回扁平列表 + 父子关系字段,前端自行渲染为树。
- GET /api/v1/workspace/read?path=<relpath>
  读取指定工作区文件的内容(截断到 200KB),供"引用"时预览。
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_runtime, require_auth

router = APIRouter(prefix="/api/v1/workspace", tags=["workspace"])

# 跳过的目录/文件(glob 模式)
_SKIP_PATTERNS = [
    ".git", ".hg", ".svn", ".idea", ".vscode", ".trae",
    ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".soul_sandbox_home", ".workbuddy",
    ".zcode", "dist", "build", "out", "target", ".DS_Store",
    "*.pyc", "*.pyo", "*.so", "*.dll", "*.exe", "*.class",
    "*.jar", "*.war", "*.png", "*.jpg", "*.jpeg", "*.gif",
    "*.ico", "*.svg", "*.woff", "*.woff2", "*.ttf", "*.eot",
    "*.mp3", "*.mp4", "*.avi", "*.mov", "*.pdf", "*.zip",
    "*.tar", "*.gz", "*.7z", "*.rar", "*.db", "*.sqlite",
    ".env*",
]


def _should_skip(name: str) -> bool:
    for pat in _SKIP_PATTERNS:
        if fnmatch.fnmatch(name, pat):
            return True
    return False


def _is_text_file(path: Path) -> bool:
    """粗略判断是否为文本文件(二进制文件不做引用)。"""
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
        # null byte = 二进制
        if b"\x00" in chunk:
            return False
        # UTF-8/ASCII 解码尝试
        chunk.decode("utf-8")
        return True
    except Exception:
        return False


@router.get("/tree", dependencies=[Depends(require_auth)])
async def list_workspace_tree(
    root: str = Query(..., description="工作区根路径(绝对)"),
    depth: int = Query(3, ge=1, le=10, description="树深度限制"),
    search: Optional[str] = Query(None, description="按文件名模糊匹配"),
    include_hidden: bool = Query(False, description="包含点开头的隐藏文件"),
):
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise HTTPException(status_code=400, detail=f"not a directory: {root}")

    items: list[dict] = []
    root_str = str(root_path)

    def walk(current: Path, level: int, parent_id: str | None):
        if level > depth:
            return
        try:
            entries = sorted(current.iterdir(),
                             key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            name = entry.name
            if not include_hidden and name.startswith("."):
                continue
            if _should_skip(name):
                continue
            entry_id = str(entry.relative_to(root_path))
            if search and search.lower() not in name.lower():
                # 仍递归进入目录,以便子文件能匹配
                if entry.is_dir():
                    walk(entry, level + 1, entry_id)
                continue
            is_dir = entry.is_dir()
            size = None
            if not is_dir:
                try:
                    size = entry.stat().st_size
                except OSError:
                    pass
            items.append({
                "id": entry_id,
                "name": name,
                "parent": parent_id,
                "is_dir": is_dir,
                "size": size,
                "relative_path": entry_id.replace("\\", "/"),
                "absolute_path": str(entry),
            })
            if is_dir:
                walk(entry, level + 1, entry_id)

    walk(root_path, 1, None)
    return {"root": root_str, "items": items}


@router.get("/read", dependencies=[Depends(require_auth)])
async def read_workspace_file(
    path: str = Query(..., description="相对工作区的路径(tree 返回的 relative_path)"),
    root: str = Query(..., description="工作区根路径"),
    max_bytes: int = Query(200_000, ge=1000, le=2_000_000),
):
    root_path = Path(root).resolve()
    target = (root_path / path).resolve()
    # 防路径穿越
    if not str(target).startswith(str(root_path)):
        raise HTTPException(status_code=400, detail="path escapes workspace")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    if not _is_text_file(target):
        raise HTTPException(status_code=400, detail="binary file, cannot reference")
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    truncated = len(content.encode("utf-8")) > max_bytes
    if truncated:
        # 按字符截断(approx)
        content = content[:max_bytes] + "\n... [truncated]"
    return {
        "path": path.replace("\\", "/"),
        "name": target.name,
        "content": content,
        "size": target.stat().st_size,
        "truncated": truncated,
    }
