"""Runtime assembly + single-process assertions (D2 / A20)."""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import threading
import time
from pathlib import Path


# --- MCP config helpers ------------------------------------------------------
_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _expand_env(value):
    """Recursively replace ``${VAR}`` with ``os.environ["VAR"]``.

    - dict:  recurse into values
    - list:  recurse into items
    - str:   replace every ``${VAR}`` occurrence; missing vars become ""
    - else:  return as-is
    """
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, str):
        def _sub(m: re.Match) -> str:
            return os.environ.get(m.group(1), "")
        return _ENV_PATTERN.sub(_sub, value)
    return value

from ..audit import AuditLog
from ..config import (
    KB_DB_PATH, KB_UPLOADS_DIR, MCP_CONFIG_PATH, Settings, SKILLS_DIR,
    SUBAGENTS_DIR,
)
from ..events import EventBus
from ..experts import ExpertStore, kb_usage_summary
from ..knowledge import (
    IngestWorker, KBStore, KBVectorStore, KnowledgeRetriever,
    OpenAICompatibleEmbedder,
)
from ..memory import MemoryDB, MemoryManager
from ..memory.pricing import price
from ..mcp import ConnectorManager, MCPBridge
from ..permissions import PermissionPolicy, WorkspaceScope, PermissionGate
from ..providers import select_provider
from ..skills import SkillRegistry
from ..storage import SessionStore
from ..subagents import SubAgentRegistry, SubAgentRunner, build_task_spec
from ..tools import build_default_registry
from ..context import build_context_layer, make_summary_provider
from ..models import SessionRecord


class Runtime:
    def __init__(self, settings: Settings | None = None,
                 bootstrap_token: str | None = None) -> None:
        self.settings = settings or Settings.load()
        self.storage = SessionStore()
        self.events = EventBus()
        self.audit = AuditLog()
        self.registry = build_default_registry()
        self.provider = select_provider(self.settings)

        # bootstrap token (A09): one-time, 60s from READY (B11).
        # Honors a token injected by the Electron shell so the shell owns it
        # (never transmitted to the renderer JS).
        if bootstrap_token:
            self.bootstrap_token = bootstrap_token
        else:
            self.bootstrap_token = secrets.token_hex(32)
        self._token_hash = hashlib.sha256(self.bootstrap_token.encode()).hexdigest()
        self._token_used = False
        self._token_ts = time.time()

        self._active_runs: dict[str, object] = {}    # session_id -> asyncio task (B10)
        self._active_agents: dict[str, object] = {}  # session_id -> SoulAgent (abort)

        # Shared per-desktop permission gate (A16/A17): the run's agent loop
        # awaits gate.wait(); the HTTP resolve endpoint calls gate.resolve().
        self.permission_gate = PermissionGate()

        # --- P3: SQLite derived index (A20) ---------------------------------
        self.db = self._open_index()
        self.memory_manager = (
            MemoryManager(self.db, on_event=lambda n, d: self.audit.append(n, d))
            if self.db is not None else None)
        self.index_status = "ok"      # "ok" | "degraded" | "rebuild"
        self.index_missing = 0
        self.check_index()
        # Regenerate user.md / user_memory.md from canonical state so the
        # settings page shows current memory even after manual file edits.
        if self.memory_manager is not None:
            self.memory_manager.refresh_user_projections()

        # --- Experts(s18):builtin + user 两层专家注册表 ---------------------
        self.experts = ExpertStore()

        # --- Knowledge base(资料库/RAG) --------------------------------------
        # 任一环节失败都只降级(kb_* = None),不拖垮 sidecar 启动。
        self.kb_store = None
        self.kb_vectors = None
        self.kb_embedder = None
        self.kb_retriever = None
        self.kb_ingest = None
        try:
            self.kb_store = KBStore(KB_DB_PATH)
            self.kb_store.ensure_default_kb()
            self.kb_embedder = OpenAICompatibleEmbedder.from_settings(self.settings)
            self.kb_vectors = KBVectorStore(self.settings.milvus_uri,
                                            self.settings.embedding_dims)
            self.kb_retriever = KnowledgeRetriever(
                self.kb_store, self.kb_vectors, self.kb_embedder)
            self.kb_ingest = IngestWorker(
                self.kb_store, self.kb_vectors, self.kb_embedder,
                KB_UPLOADS_DIR, chunk_tokens=self.settings.kb_chunk_tokens)
            self.kb_ingest.start()
            self.kb_ingest.enqueue_pending()
        except Exception as exc:
            self.audit.append("kb_init_failed", {"error": str(exc)})

        # --- P5: MCP connectors (untrusted + disconnected by default) -------
        self.mcp = ConnectorManager(self._load_mcp_config())
        self.mcp_bridge = MCPBridge(self.mcp)        # Auto-connect every configured connector so the model can use MCP
        # tools without a manual UI toggle — but in the BACKGROUND: an npx
        # stdio connector takes 20-30s to spawn/handshake (observed 22-31s
        # for github), far past Electron's 15s SOULBUDDY_READY budget, so
        # this must not sit on the startup critical path. Tools appear once
        # the thread binds them; per-connect failures are logged, non-fatal.
        threading.Thread(target=self._auto_connect_mcp, daemon=True,
                         name="mcp-auto-connect").start()

    def _load_mcp_config(self) -> dict:
        """Load ~/.soul_buddy/mcp.json with ${VAR} env substitution.

        Values like ``"${GITHUB_TOKEN}"`` are replaced with the actual OS env
        var at load time. Missing vars become empty strings (no crash, but the
        connector will likely fail to connect — the user sees that in the UI).
        """
        import json
        import re

        path = MCP_CONFIG_PATH
        try:
            if path and Path(path).exists():
                raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
                return _expand_env(raw)
        except Exception as exc:
            self.audit.append("mcp_config_error", {"error": str(exc)})
        return {}

    def _auto_connect_mcp(self) -> None:
        """Trust + connect every configured connector (runs in a daemon thread).

        Failures are logged and swallowed — a single bad connector must never
        prevent the rest of the app from booting. Connected tools are bound
        into the shared registry so the model sees them on the next turn.
        """
        import logging
        log = logging.getLogger("soul_buddy.runtime")
        bound_total = 0
        for name in list(self.mcp.connectors.keys()):
            try:
                self.mcp.trust(name)
                ok = self.mcp.connect(name)
                if ok:
                    self.mcp.refresh_grant()
                    bound = self.mcp_bridge.bind(self.registry)
                    bound_total += bound
                    log.info("auto-connected MCP %s (%d tools)", name, bound)
                    self.audit.append("mcp_auto_connected",
                                      {"connector": name, "tools": bound})
                else:
                    log.warning("MCP %s connect returned False — check config", name)
                    self.audit.append("mcp_auto_connect_failed",
                                      {"connector": name, "reason": "connect_false"})
            except Exception as exc:
                log.warning("MCP %s auto-connect failed: %s", name, exc)
                self.audit.append("mcp_auto_connect_failed",
                                  {"connector": name, "error": str(exc)})
        if bound_total:
            log.info("MCP auto-bind complete: %d tools from %d connector(s)",
                     bound_total,
                     sum(1 for c in self.mcp.connectors.values()
                         if c.status == "connected"))

    # --- index lifecycle (A20) ---------------------------------------------
    def _open_index(self) -> MemoryDB | None:
        try:
            return MemoryDB()
        except Exception as exc:  # corrupt/unopenable DB -> rebuild from JSONL
            self.audit.append("index_rebuilt",
                              {"detail": "db open failed; will rebuild",
                               "error": str(exc)})
            return None

    def check_index(self) -> None:
        """A20: reconcile the SQLite index against JSONL truth.

        Drift is only *reported* (health degraded + audit_gap) — never
        auto-fixed. A completely unopenable DB is auto-rebuilt (self-healing).
        """
        if self.db is None:
            # Attempt recovery from the JSONL source of truth.
            try:
                self.db = MemoryDB()
                self.memory_manager = MemoryManager(
                    self.db, on_event=lambda n, d: self.audit.append(n, d))
                self.db.rebuild_from_storage(self.storage)
                self.audit.append("index_rebuilt",
                                  {"detail": "recovered from JSONL"})
            except Exception as exc:
                self.index_status = "degraded"
                self.index_missing = -1
                self.audit.append("index_gap",
                                  {"reason": "index_unrecoverable",
                                   "error": str(exc)})
                return
        try:
            ok, missing = self.db.reconcile(self.storage)
        except Exception as exc:
            self.index_status = "degraded"
            self.index_missing = -1
            self.audit.append("index_gap",
                              {"reason": "reconcile_error", "error": str(exc)})
            return
        if ok:
            self.index_status = "ok"
            self.index_missing = 0
        else:
            self.index_status = "degraded"
            self.index_missing = missing
            self.audit.append("audit_gap",
                              {"reason": "index_drift", "missing": missing})

    # --- session ------------------------------------------------------------
    def create_session(self, workspace_root: str, cwd: str | None = None,
                       provider: str | None = None,
                       title: str | None = None) -> SessionRecord:
        rec = SessionRecord.create(workspace_root, cwd,
                                   provider or self.settings.provider or "offline",
                                   title=title)
        self.storage.save_session(rec)
        # Keep the derived SQLite index in sync (A20).
        if self.db is not None:
            try:
                self.db.upsert_session(rec)
            except Exception as exc:
                self.audit.append("index_gap",
                                  {"reason": "upsert_session_failed",
                                   "error": str(exc)})
        return rec

    def get_session(self, session_id: str) -> SessionRecord | None:
        return self.storage.get_session(session_id)

    def update_session(self, session_id: str, **fields) -> SessionRecord | None:
        """Patch mutable metadata (currently: title). Bumps updated_at."""
        import time as _time

        rec = self.storage.get_session(session_id)
        if rec is None:
            return None
        for k, v in fields.items():
            if hasattr(rec, k):
                setattr(rec, k, v)
        rec.updated_at = _time.time()
        self.storage.save_session(rec)
        if self.db is not None:
            try:
                self.db.upsert_session(rec)
            except Exception as exc:
                self.audit.append("index_gap",
                                  {"reason": "upsert_session_failed",
                                   "error": str(exc)})
        return rec

    def delete_session(self, session_id: str) -> bool:
        """Abort any in-flight run, then drop the session directory."""
        task = self._active_runs.get(session_id)
        if task is not None and not task.done():
            gate_abort = getattr(self.permission_gate, "abort_pending", None)
            if callable(gate_abort):
                try:
                    gate_abort()
                except Exception:
                    pass
            task.cancel()
        self._active_runs.pop(session_id, None)
        self._active_agents.pop(session_id, None)
        if self.db is not None:
            try:
                self.db.delete_session(session_id)
            except Exception as exc:
                self.audit.append("index_gap",
                                  {"reason": "delete_session_failed",
                                   "error": str(exc)})
        return self.storage.delete_session(session_id)

    # --- per-session policy + agent builder --------------------------------
    def build_agent(self, session: SessionRecord, approver):
        from ..agent import SoulAgent
        scope = WorkspaceScope(Path(session.workspace_root))
        policy = PermissionPolicy(scope)
        # Per-session provider override: "auto" or empty falls back to global default.
        session_provider = session.provider if session.provider and session.provider != "auto" else self.settings.provider
        provider = select_provider(self.settings, force_name=session_provider)
        # 专家绑定(s18):先加载专家,因为 replace_core 专家需要替换 role 段
        expert = self.experts.get(getattr(session, "expert_id", None))
        if expert is not None and not expert.enabled:
            expert = None
        # replace_core 专家:用专家 system_prompt 替换默认的核心身份
        role_override = None
        if expert is not None and expert.replace_core and expert.system_prompt:
            role_override = expert.system_prompt
        # P0-1: wire the L4 summary layer to the session's provider — without
        # it, long sessions degrade to pure truncation and lose intent.
        context = build_context_layer(
            summary_provider=make_summary_provider(provider),
            on_event=lambda name, data: self.audit.append(
                "context_event", {"name": name, **data}),
            memory=self.memory_manager,
            audit=self.audit,
            role_override=role_override,
        )
        # P5: skills — user-level (~/.soul_buddy/skills) + project-level
        skills = SkillRegistry(workspace_root=session.workspace_root,
                               user_dir=SKILLS_DIR)
        # Sub-agents — 三层:builtin(随包) < user(~/.soul_buddy) < project(工作区)
        # builtin_dir 默认取 BUILTIN_SUBAGENTS_DIR(soul_buddy/subagents/builtin)
        subagents = SubAgentRegistry(workspace_root=session.workspace_root,
                                    user_dir=SUBAGENTS_DIR)
        # 把可用 sub-agent 类型列表注入 task 工具的 schema description,
        # 让模型在 tool spec 里直接看到可选类型(如 "explore"),不需要
        # 翻 system prompt 的 index_block。桌面单用户场景,session 顺序执行。
        if subagents.names():
            self.registry._specs["task"] = build_task_spec(subagents.names())
        # Runner factory: closures over runtime-owned deps so the agent loop
        # can lazily construct a fresh SubAgentRunner per delegation.
        runner_factory = (lambda settings=self.settings, audit=self.audit,
                          storage=self.storage:
                          SubAgentRunner(settings, audit, storage))
        # 资料库绑定:专家 kb_ids 里仍然存在的库 -> 检索链路(依赖 embedding 配置)。
        kb_summary = None
        knowledge = None
        kb_ids: list[str] = []
        if expert is not None and expert.kb_ids and self.kb_store is not None:
            kb_ids = [k for k in expert.kb_ids if self.kb_store.get_kb(k)]
            kb_names = [self.kb_store.get_kb(k)["name"] for k in kb_ids]
            if kb_ids and self.kb_retriever is not None \
                    and self.kb_retriever.available():
                knowledge = self.kb_retriever
                kb_summary = kb_usage_summary(kb_names)
        # replace_core 专家:不再追加 expert_block,因为已经替换了 role 段
        # 非 replace_core 专家:保留叠加模式,追加 expert_block
        return SoulAgent(self.storage, self.registry, self.events, self.audit,
                         provider, policy, context=context,
                         memory=self.memory_manager, skills=skills,
                         stream=True, subagents=subagents,
                         subagent_runner_factory=runner_factory,
                         expert=expert, kb_summary=kb_summary,
                         knowledge=knowledge, kb_ids=kb_ids)

    # --- bootstrap (A09 / B11) ---------------------------------------------
    def consume_bootstrap(self, token: str) -> bool:
        if self._token_used:
            self.audit.append("bootstrap_replay", {"detail": "token already used"})
            return False
        if time.time() - self._token_ts > 60:
            return False
        if hashlib.sha256(token.encode()).hexdigest() != self._token_hash:
            return False
        self._token_used = True
        self.audit.append("bootstrap_ok", {"detail": "token consumed"})
        return True


def assert_single_worker() -> None:
    """D2: EventBus is in-process; multiple workers silently break SSE."""
    workers = os.environ.get("SOUL_WORKERS", "1")
    assert workers in ("1", "", None), "soul_buddy sidecar must run with workers=1"
