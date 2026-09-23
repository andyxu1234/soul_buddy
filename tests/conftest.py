"""Shared fixtures. Isolates all state under a temp SOUL_BUDDY_HOME."""
import os
import tempfile

# Must be set BEFORE importing the package so config reads it.
# config.py loads .env at import time (override=False), so anything pinned here
# wins over whatever the developer's .env happens to say.
#
# Third-party imports may also call load_dotenv() on their own, which would pull
# the developer's .env into os.environ even though config.py honours the skip
# switch. Pinning these first keeps the suite hermetic either way, because
# load_dotenv(override=False) never overwrites an existing variable.
for _var in ("MILVUS_URI", "EMBEDDING_BASE_URL", "EMBEDDING_API_KEY",
             "EMBEDDING_MODEL", "EMBEDDING_DIMS", "SOUL_PROVIDER",
             "TYPESAFE_API_KEY"):
    os.environ[_var] = ""

_TMP_HOME = tempfile.mkdtemp(prefix="soul_test_home_")
os.environ["SOUL_BUDDY_HOME"] = _TMP_HOME
# Belt and braces: config.py skips the .env lookup entirely under this switch,
# so its module-level constants (RUBRIC_MODE, LOG_LEVEL, …) stay at defaults.
os.environ["SOUL_SKIP_DOTENV"] = "1"
# Rubric is driven through an explicit RubricPolicy in tests, never via config,
# but pin it anyway so a shell-exported value can't leak in either.
os.environ["SOUL_RUBRIC_MODE"] = "off"
# The TypeSafe backend reaches the network. Pin it off so that a developer with
# SOUL_RUBRIC_JUDGE_BACKEND=typesafe exported (and a real key) cannot turn the
# suite into a paid, flaky integration test. tests/test_rubric_typesafe.py
# injects a stub transport instead.
os.environ["SOUL_RUBRIC_JUDGE_BACKEND"] = "llm"

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
                events=None, provider=None, stream: bool = False, rubric=None,
                rubric_provider=None):
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
                      stream=stream, rubric=rubric,
                      rubric_provider=rubric_provider)
    return agent, session, storage


class _EventsStub:
    """In-tests we assert via storage transcript, so the bus is a no-op stub."""

    async def publish(self, *a, **k):
        return None


@pytest.fixture
def make_agent(workspace):
    def _f(script=None, default=None, events=None, provider=None,
           stream=False, rubric=None, rubric_provider=None):
        return _make_agent(workspace, script, default, events, provider,
                           stream, rubric, rubric_provider)
    return _f
