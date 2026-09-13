"""Rollback tools — 把改动恢复到改前状态,基于 file-history-snapshot 快照。

三个粒度:
  list_changes    — 列出本会话所有可回滚的变更(checkpoint + 文件 + 版本号)
  rollback_file   — 文件级: 把某个文件恢复到指定版本
  rollback_session — 会话级: 把所有被改过的文件恢复到初始状态(最早快照)
"""
from __future__ import annotations

from ..models import ToolResult


def _get_fh(ctx) -> object | None:
    """从 ToolContext 拿到 FileHistoryStore(通过 ctx.storage.file_history)。"""
    storage = getattr(ctx, "storage", None)
    if storage is None:
        return None
    return getattr(storage, "file_history", None)


def run_list_changes(args: dict, ctx) -> ToolResult:
    """列出本会话所有可回滚的变更。

    返回每个 checkpoint 的 request_id、工具名、涉及文件及快照版本号。
    LLM 应该在决定回滚之前先调用它,了解有哪些选项。
    """
    fh = _get_fh(ctx)
    if fh is None:
        return ToolResult(
            content="回滚功能未启用: FileHistoryStore 不可用", is_error=True)

    idx = fh.read_changes_index(ctx.session_id)
    changes = idx.get("changes", [])
    if not changes:
        return ToolResult(content="本会话暂无可回滚的文件变更。")

    summary_lines = []
    for c in changes:
        cid = c.get("checkpointId", "?")
        tool_name = c.get("tool", "?")
        ts = c.get("timestamp", 0)
        files = c.get("files", [])
        file_desc = ", ".join(
            f"{f['filePath'].split('/')[-1]}(v{f['backup']['version']})"
            for f in files if f.get("backup")
        )
        summary_lines.append(
            f"  [{cid}] {tool_name} at {ts}: {file_desc}")

    return ToolResult(
        content="本会话有以下可回滚变更:\n" + "\n".join(summary_lines)
    )


def run_rollback_file(args: dict, ctx) -> ToolResult:
    """文件级回滚: 把某个文件恢复到指定快照版本。

    参数:
      path:    文件路径(绝对路径或相对 workspace_root)
      version: 要恢复的快照版本号(从 list_changes 结果里的 backup.version 取)
    """
    fh = _get_fh(ctx)
    if fh is None:
        return ToolResult(
            content="回滚功能未启用: FileHistoryStore 不可用", is_error=True)

    rel = args.get("path", "")
    version = args.get("version")

    if not rel:
        return ToolResult(content="INVALID_ARGUMENTS: path is required", is_error=True)
    if version is None:
        return ToolResult(content="INVALID_ARGUMENTS: version is required", is_error=True)

    # 相对路径 -> 绝对路径
    from pathlib import Path
    abs_path = Path(rel)
    if not abs_path.is_absolute():
        abs_path = (ctx.workspace_root / abs_path).resolve()

    result = fh.rollback_file(ctx.session_id, str(abs_path), int(version))
    if result.get("ok"):
        return ToolResult(
            content=f"已回滚 {result['filePath']} 到版本 v{result['version']} "
                    f"({result['restored_bytes']} bytes restored)")
    else:
        return ToolResult(content=f"回滚失败: {result.get('error', 'unknown')}",
                          is_error=True)


def run_rollback_session(args: dict, ctx) -> ToolResult:
    """会话级回滚: 把本会话所有被工具改过的文件恢复到初始状态。

    这是最彻底的回滚——会把所有文件恢复到它们第一次被改动之前的版本。
    不可逆,请确认后再调用。
    """
    fh = _get_fh(ctx)
    if fh is None:
        return ToolResult(
            content="回滚功能未启用: FileHistoryStore 不可用", is_error=True)

    result = fh.rollback_session(ctx.session_id)
    restored = result.get("restored", [])
    if restored:
        lines = [f"  {r['filePath'].split('/')[-1]} ← v{r['version']}"
                 for r in restored]
        return ToolResult(
            content=f"已回滚 {len(restored)} 个文件:\n" + "\n".join(lines))
    else:
        return ToolResult(content="本会话暂无可回滚的文件变更。")
