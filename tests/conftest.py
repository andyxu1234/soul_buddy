"""Shared fixtures. Isolates all state under a temp SOUL_BUDDY_HOME."""
import os
import tempfile

# Must be set BEFORE importing the package so config reads it.
_TMP_HOME = tempfile.mkdtemp(prefix="soul_test_home_")
os.environ["SOUL_BUDDY_HOME"] = _TMP_HOME

import pytest  # noqa: E402
from pathlib import Path  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from soul_buddy.audit import AuditLog  # noqa: E402
from soul_buddy.permissions import PermissionPolicy, WorkspaceScope  # noqa: E402
from soul_buddy.providers.base import ModelTurn, ToolCall  # noqa: E402
from soul_buddy.providers.offline import OfflineProvider  # noqa: E402
from soul_buddy.permissions import AutoApproveGate  # noqa: E402
from soul_buddy.storage import SessionStore  # noqa: E402
from soul_buddy.tools import build_default_registry  # noqa: E402
from soul_buddy.models import SessionRecord  # noqa: E402
from soul_buddy.agent import SoulAgent  # noqa: E402
from soul_buddy.api.main import create_app  # noqa: E402


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("hello", encoding="utf-8")
    (ws / "b.txt").write_text("foo\nfoo\nfoo\nbar", encoding="utf-8")
    (ws / "sub").mkdir()
    (ws / "sub" / "c.py").write_text("print('x')", encoding="utf-8")
    return ws


def _make_agent(workspace: Path, script=None, default: ModelTurn | None = None,
                events=None, provider=None, stream: bool = False):
    storage = SessionStore()
    audit = AuditLog()
    if provider is None:
        provider = OfflineProvider()
        if script is not None:
            provider.set_script(script)
        if default is not None:
            provider.set_default(default)
    scope = WorkspaceScope(workspace)
    policy = PermissionPolicy(scope)
    session = SessionRecord.create(str(workspace))
    # 生产路径 runtime.create_session 先落 session.json,把会话目录钉在
    # workspace slug 下;测试同样必须先 save,否则首条事件会走 default
    # fallback 目录,与工具层创建的目录分裂(transcript 被拆成两半)。
    storage.save_session(session)
    registry = build_default_registry()
    events = events or _EventsStub()
    agent = SoulAgent(storage, registry, events, audit, provider, policy,
                      stream=stream)
    return agent, session, storage


class _EventsStub:
    """In-tests we assert via storage transcript, so the bus is a no-op stub."""

    async def publish(self, *a, **k):
        return None


@pytest.fixture
def make_agent(workspace):
    def _f(script=None, default=None, events=None, provider=None, stream=False):
        return _make_agent(workspace, script, default, events, provider, stream)
    return _f
