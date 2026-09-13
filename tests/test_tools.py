"""Filesystem + bash tools: behavior, scope, backup, dispatch (BR-05/19, A25, INV-10)."""
import pytest
from pathlib import Path

from soul_buddy.config import FILE_HISTORY_DIR
from soul_buddy.tools.registry import ToolContext, build_default_registry
from soul_buddy.tools import fs, bash
from soul_buddy.permissions.scope import WorkspaceScope
from soul_buddy.context.externalize import Externalizer
from soul_buddy.providers.base import ToolCall


@pytest.fixture
def toolctx(workspace, tmp_path):
    return ToolContext(
        session_id="s1",
        workspace_root=workspace,
        cwd=workspace,
        scope=WorkspaceScope(workspace),
        backup_root=tmp_path / "backups",
        externalizer=Externalizer(tmp_path / "tool-results"),
        bash_timeout=60,
    )


def test_read_file(toolctx):
    r = fs.run_read_file({"path": "a.txt"}, toolctx)
    assert r.content == "hello"
    assert not r.is_error


def test_read_file_missing(toolctx):
    r = fs.run_read_file({"path": "nope.txt"}, toolctx)
    assert r.is_error and "not found" in r.content


def test_read_file_escape(toolctx):
    r = fs.run_read_file({"path": "../../escape.txt"}, toolctx)
    assert r.is_error and "escapes workspace" in r.content


def test_write_file_creates(toolctx, workspace):
    r = fs.run_write_file({"path": "new.txt", "content": "xyz"}, toolctx)
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "xyz"


def test_write_file_backup(toolctx, workspace, tmp_path):
    # workspace fixture 中 b.txt 已存在,内容 "foo\nfoo\nfoo\nbar"
    original = (workspace / "b.txt").read_text(encoding="utf-8")
    fs.run_write_file({"path": "b.txt", "content": "v1"}, toolctx)
    fs.run_write_file({"path": "b.txt", "content": "v2"}, toolctx)
    # 备份现在存到 FILE_HISTORY_DIR/<session_id>/<hash>@vN
    backups = sorted((FILE_HISTORY_DIR / "s1").glob("*@v*"))
    assert len(backups) >= 2, "expected backups of the overwritten file"
    # v1 备份存的是原始内容
    assert backups[0].read_text(encoding="utf-8") == original
    # v2 备份存的是 v1
    assert backups[1].read_text(encoding="utf-8") == "v1"
    # metadata 应携带 file_backup 信息
    r = fs.run_write_file({"path": "b.txt", "content": "v3"}, toolctx)
    fb = r.metadata.get("file_backup")
    assert fb is not None
    assert fb["version"] == 3          # v1=原始, v2=v1内容, v3=v2内容
    assert "@v" in fb["backupFileName"]
    # 当前文件内容应为 v3
    assert (workspace / "b.txt").read_text(encoding="utf-8") == "v3"


def test_edit_ambiguous(toolctx):
    r = fs.run_edit_file({"path": "b.txt", "old_string": "foo", "new_string": "baz"}, toolctx)
    assert r.is_error and "AMBIGUOUS_MATCH" in r.content


def test_edit_expected_count(toolctx, workspace):
    r = fs.run_edit_file({"path": "b.txt", "old_string": "foo", "new_string": "baz",
                          "expected_count": 3}, toolctx)
    assert not r.is_error
    assert (workspace / "b.txt").read_text(encoding="utf-8").count("baz") == 3


def test_edit_replace_all(toolctx, workspace):
    r = fs.run_edit_file({"path": "b.txt", "old_string": "foo", "new_string": "baz",
                          "replace_all": True}, toolctx)
    assert not r.is_error
    assert "foo" not in (workspace / "b.txt").read_text(encoding="utf-8")


def test_glob(toolctx):
    r = fs.run_glob({"pattern": "**/*.txt"}, toolctx)
    assert "a.txt" in r.content


def test_grep(toolctx):
    r = fs.run_grep({"pattern": "bar"}, toolctx)
    assert "b.txt" in r.content


def test_dispatch_unknown_tool(toolctx):
    reg = build_default_registry()
    r = reg.dispatch(ToolCall(id="x", name="nope", arguments={}), toolctx)
    assert r.is_error and "UNKNOWN_TOOL" in r.content


def test_dispatch_missing_arg(toolctx):
    reg = build_default_registry()
    r = reg.dispatch(ToolCall(id="x", name="read_file", arguments={}), toolctx)
    assert r.is_error and "INVALID_ARGUMENTS" in r.content


def test_dispatch_exception_is_data(toolctx):
    reg = build_default_registry()

    def boom(args, ctx):
        raise RuntimeError("boom")

    reg._handlers["read_file"] = boom
    r = reg.dispatch(ToolCall(id="x", name="read_file", arguments={"path": "a.txt"}), toolctx)
    assert r.is_error and "boom" in r.content


def test_bash_simple(toolctx):
    r = bash.run_bash({"command": "echo hello"}, toolctx)
    assert "hello" in r.content
    assert "[exit_code=0]" in r.content


def test_bash_multiline_rejected(toolctx):
    r = bash.run_bash({"command": "echo a\necho b"}, toolctx)
    assert r.is_error and "multi-line" in r.content
