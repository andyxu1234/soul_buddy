"""Filesystem tools — read / write / edit / glob / grep (BR-05 / A07 / A25 / INV-10).

All paths are validated against the workspace scope (double-guarded even though
the permission layer also checks). Writes/edits back up the previous version
before mutating (INV-10). `edit_file` never guesses on ambiguous matches (A25).

Backup layout (WorkBuddy-aligned, three-layer storage):
  内容层: <HOME>/file-history/<session_id>/<hash>@<vN>
  hash = sha256(绝对路径).hexdigest()[:16]
  快照存的是"改动前"的完整内容,回滚 = 把某份 @vN 覆盖回原路径。
"""
from __future__ import annotations

import hashlib
import re
import shutil
import time
from pathlib import Path

from ..config import FILE_HISTORY_DIR
from ..models import ToolResult


def _file_hash(abs_path: str) -> str:
    """sha256(绝对路径)[:16] — 只依赖路径,不依赖内容,无需映射表即可定位。"""
    return hashlib.sha256(str(abs_path).encode()).hexdigest()[:16]


def _snapshot_before_write(ctx, target: Path) -> dict | None:
    """改前备份: 把 target 原内容复制到 file-history/<sid>/<hash>@<vN>。

    返回 backup metadata(供 agent 层 emit file-history-snapshot 事件用);
    文件不存在时返回 None(新建文件无备份)。
    """
    if not target.exists():
        return None
    abs_path = str(target.resolve())
    h = _file_hash(abs_path)
    bak_dir = FILE_HISTORY_DIR / ctx.session_id
    bak_dir.mkdir(parents=True, exist_ok=True)
    # 推算下一版本号: 扫描该 hash 已有的 @vN,取最大 +1
    existing = sorted(bak_dir.glob(f"{h}@v*"))
    next_v = (max(int(p.name.split("@v")[1]) for p in existing) + 1
              if existing else 1)
    dst = bak_dir / f"{h}@v{next_v}"
    shutil.copy2(target, dst)
    return {
        "backupFileName": dst.name,
        "version": next_v,
        "hash": h,
        "backupTime": int(time.time() * 1000),
        "filePath": abs_path,
    }


def run_read_file(args: dict, ctx) -> ToolResult:
    sp = ctx.scope.safe_path(args.get("path", ""), ctx.cwd)
    if sp is None:
        return ToolResult(content=f"Error: path escapes workspace: {args.get('path')}",
                         is_error=True)
    if not sp.exists():
        return ToolResult(content=f"Error: file not found: {sp}", is_error=True)
    try:
        text = sp.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return ToolResult(content=f"Error reading file: {exc}", is_error=True)
    return ToolResult(content=ctx.externalizer.externalize(text, ctx.session_id))


def run_write_file(args: dict, ctx) -> ToolResult:
    sp = ctx.scope.safe_path(args.get("path", ""), ctx.cwd)
    if sp is None:
        return ToolResult(content=f"Error: path escapes workspace: {args.get('path')}",
                         is_error=True)
    content = args.get("content", "")
    existed = sp.exists()
    backup = _snapshot_before_write(ctx, sp) if existed else None
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(content, encoding="utf-8")
    verb = "overwrote" if existed else "created"
    return ToolResult(
        content=f"OK: {verb} {sp} ({len(content)} chars)",
        metadata={
            "overwrite": existed,
            "path": str(sp),
            "action": "modify" if existed else "create",
            "file_backup": backup,
        })


def run_edit_file(args: dict, ctx) -> ToolResult:
    sp = ctx.scope.safe_path(args.get("path", ""), ctx.cwd)
    if sp is None:
        return ToolResult(content=f"Error: path escapes workspace: {args.get('path')}",
                         is_error=True)
    if not sp.exists():
        return ToolResult(content=f"Error: file not found: {sp}", is_error=True)
    old = args.get("old_string", "")
    new = args.get("new_string", "")
    if not old:
        return ToolResult(content="Error: old_string is required", is_error=True)
    text = sp.read_text(encoding="utf-8", errors="replace")

    # Normalize line endings for matching
    norm = text.replace("\r\n", "\n")
    count = norm.count(old)
    if count == 0:
        head = "\n".join(norm.splitlines()[:20])
        return ToolResult(
            content=f"OLD_STRING_NOT_FOUND. Showing first 20 lines for context:\n{head}",
            is_error=True)
    if count > 1 and not args.get("replace_all") and args.get("expected_count") != count:
        lines = norm.splitlines()
        hits = [i + 1 for i, ln in enumerate(lines) if old in ln]
        return ToolResult(
            content=f"AMBIGUOUS_MATCH: '{old}' found {count} times at lines "
                    f"{hits}. Use replace_all=true or set expected_count={count}.",
            is_error=True)

    backup = _snapshot_before_write(ctx, sp)
    if args.get("replace_all") or args.get("expected_count") == count:
        new_text = norm.replace(old, new)
    else:
        new_text = norm.replace(old, new, 1)
    sp.write_text(new_text, encoding="utf-8")
    return ToolResult(
        content=f"OK: edited {sp} ({count} replacement(s))",
        metadata={
            "path": str(sp),
            "action": "modify",
            "file_backup": backup,
        })


def run_glob(args: dict, ctx) -> ToolResult:
    pattern = args.get("pattern", "**/*")
    try:
        matches = sorted(p for p in ctx.scope.root.glob(pattern) if p.is_file())
    except Exception as exc:
        return ToolResult(content=f"Error: {exc}", is_error=True)
    if not matches:
        return ToolResult(content="(no files matched)")
    rels = [str(p.relative_to(ctx.scope.root).as_posix()) for p in matches]
    return ToolResult(content="\n".join(rels))


def run_grep(args: dict, ctx) -> ToolResult:
    pattern = args.get("pattern", "")
    if not pattern:
        return ToolResult(content="Error: pattern required", is_error=True)
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return ToolResult(content=f"Error: invalid regex: {exc}", is_error=True)
    root = ctx.scope.root
    found = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix in {".exe", ".dll", ".png", ".jpg", ".bin"}:
            continue
        try:
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if rx.search(line):
                    found.append(f"{p.relative_to(root).as_posix()}:{i}: {line}")
        except Exception:
            continue
    if not found:
        return ToolResult(content="(no matches)")
    return ToolResult(content="\n".join(found[:200]))
