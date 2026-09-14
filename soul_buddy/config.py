"""Global configuration & constants for soul_buddy.

Single source of truth for limits, thresholds and directory layout.
All values referenced by the implementation plan (§4.1 / §5.5) and the
clarification answers (A05–A26) live here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --- Agent loop limits -------------------------------------------------------
MAX_TURNS = 40                      # BR-01
TURN_BUDGET_WARNING = 32            # BR-01: 80% -> emit turn_budget_warning
REPEAT_CALL_LIMIT = 3              # BR-02: same (tool, args) >= 3 -> deny (per-run scope, A02)

# --- First-turn reasoning (方案 B) ------------------------------------------
# 第一轮如果模型直接出 tool_calls 且 text 长度 < 该阈值,强制它先输出推理再调工具。
FIRST_TURN_REASONING_MIN_LEN = 20    # 字符数,低于此值视为"没有推理"
FIRST_TURN_REASONING_MAX_RETRIES = 2  # 最多强制重试几次,超过则放行(避免死循环)

# --- Context window per provider (A13) --------------------------------------
CONTEXT_WINDOW = {
    "deepseek": 64_000,
    "anthropic": 200_000,
    "openai": 128_000,
    "offline": 8_000,
}
COMPACT_TRIGGER_RATIO = 0.75
COMPACT_TARGET_RATIO = 0.50
RESERVE_FOR_OUTPUT = 4_096
SUMMARY_INPUT_MAX_CHARS = 30_000    # dropped-history text sent to the summarizer (tail kept)
SUBAGENT_KEEP_RECENT_TURNS = 4      # sub-agents are ephemeral workers: smaller history floor

# --- Output externalization (A14 / BR-06) -----------------------------------
EXTERNALIZE_THRESHOLD_BYTES = 50 * 1024     # 50 KiB, strict greater-than
EXTERNALIZE_PREVIEW_BYTES = 2 * 1024        # 2 KiB preview, UTF-8 safe truncation

# --- Bash output bounding (A14 extension: bash is the biggest context
#     consumer; read_file already externalizes, bash did not) ------------------
BASH_INLINE_MAX_CHARS = 20_000      # above this, inline output is head+tail trimmed
BASH_INLINE_HEAD_CHARS = 14_000     # kept head when trimming
BASH_INLINE_TAIL_CHARS = 5_000      # kept tail when trimming (errors live at the end)

# --- Concurrency / runtime (A19 / B06) -------------------------------------
MAX_CONCURRENT_RUNS = 4             # BR-34: simultaneous *running* runs, not sessions

# --- Bash execution (A24 / BR-26) ------------------------------------------
BASH_TIMEOUT = 60                   # default seconds
BASH_TIMEOUT_MAX = 300              # hard cap, server-side only (B16: not in schema)

# --- Permissions (A05 / A26) ------------------------------------------------
PERMISSION_TTL_DAYS = 30

# --- Sidecar lifecycle (A18 / B11 / B12) -----------------------------------
BOOTSTRAP_TTL = 60                  # seconds, counted from SOULBUDDY_READY (B11)
HEARTBEAT_INTERVAL = 5              # seconds (Electron writes runtime.json heartbeat)
HEARTBEAT_TIMEOUT = 15             # seconds (sidecar self-kills if now-hb > this)

# --- Audit lock (A19 / B04) ------------------------------------------------
AUDIT_LOCK_TIMEOUT = 5             # seconds before degraded (don't block loop)

# --- Externalization quota (A15 / BR-24) ----------------------------------
QUOTA_SESSION_MAX_BYTES = 200 * 1024 * 1024
QUOTA_SESSION_MAX_FILES = 500
QUOTA_GLOBAL_MAX_BYTES = 2 * 1024 * 1024 * 1024


def _resolve_home() -> Path:
    raw = os.environ.get("SOUL_BUDDY_HOME")
    if raw:
        p = Path(raw).expanduser()
    else:
        p = Path.home() / ".soul_buddy"
    p.mkdir(parents=True, exist_ok=True)
    return p


HOME = _resolve_home()                       # ~/.soul_buddy
SESSIONS_DIR = HOME / "sessions"            # <home>/sessions/<id>/  (legacy, kept for migration)
PROJECTS_DIR = HOME / "projects"           # <home>/projects/<slug>/<id>/
DEFAULT_WORK_DIR = HOME / "default"        # workspace_root fallback when user picks no dir
AUDIT_DIR = HOME / "audit"                  # <home>/audit/audit.log + anchor
PERMISSIONS_PATH = HOME / "permissions.json"
RUNTIME_JSON = HOME / "runtime.json"
LOG_DIR = HOME / "logs"                     # sidecar 运行日志
SIDECAR_LOG = LOG_DIR / "sidecar.log"       # 主日志（轮转）

# --- Logging ---
# 按天滚动：当天日志写入 sidecar.log，午夜滚动后历史日志命名为 sidecar.log.YYYY-MM-DD
LOG_BACKUP_DAYS = 14                        # 保留最近 14 天的历史日志
LOG_LEVEL = os.environ.get("SOUL_LOG_LEVEL", "INFO")

# --- P5: skills + MCP ------------------------------------------------------
SKILLS_DIR = HOME / "skills"                # user-level skills: <home>/skills/<n>/SKILL.md
MCP_CONFIG_PATH = HOME / "mcp.json"         # MCP connector config (s17 shape)

# --- Sub-agents (Task tool / orchestrator pattern) -------------------------
SUBAGENTS_DIR = HOME / "subagents"         # user-level: <home>/subagents/<n>/agent.yaml
SUBAGENT_FILENAME = "agent.yaml"
SUBAGENT_MAX_TURNS = 10                     # default per-subagent turn cap (BR-01 子层)
SUBAGENT_MAX_TIME_S = 300                   # 5 min hard cap per delegation
SUBAGENT_FORBIDDEN_TOOLS = frozenset({
    "task",                                 # 不可递归:sub-agent 不能再派 sub-agent
    "present_files",                        # 产物交付由主 Agent 统一处理
    "rollback_file", "rollback_session",    # 回滚是主会话语义,子代理不应触发
    "list_changes",
    # 记忆写入是主会话语义:子代理(探索型)不应替用户产生长期记忆,
    # 且 provenance 应归属主会话。
    "save_user_preference", "write_workspace_fact",
    # 资料库检索绑定在主会话专家上,子代理 ctx 无 knowledge
    "search_knowledge",
})

# 内置 sub-agents(随包分发,开箱即用):soul_buddy/subagents/builtin/
# 由 Path(__file__).parent / "subagents" / "builtin" 计算,不写死 home。
def _builtin_subagents_dir() -> Path:
    return Path(__file__).parent / "subagents" / "builtin"

BUILTIN_SUBAGENTS_DIR = _builtin_subagents_dir()

# --- Experts(s18:预设角色包,单层 user) --------------------------------------
EXPERTS_DIR = HOME / "experts"             # <home>/experts/<id>.json

# --- Knowledge base(资料库/RAG) ----------------------------------------------
KB_DIR = HOME / "kb"                       # <home>/kb/{kb.db, milvus.db, uploads/<kb_id>/}
KB_DB_PATH = KB_DIR / "kb.db"             # 元数据(stdlib sqlite3,自包含)
MILVUS_DB_PATH = KB_DIR / "milvus.db"     # milvus-lite 本地库文件
KB_UPLOADS_DIR = KB_DIR / "uploads"       # 上传原文:<uploads>/<kb_id>/<doc_id><ext>
KB_UPLOAD_LIMIT_MB = 30                    # 单文件上传上限
KB_ALLOWED_EXTS = {".md", ".markdown", ".txt", ".pdf", ".docx"}

# --- File history (WorkBuddy-aligned, three-layer storage) ----------------
# 内容层: 完整文件快照,文件名 <hash>@<vN>,hash=sha256(绝对路径)[:16]
FILE_HISTORY_DIR = HOME / "file-history"    # <home>/file-history/<session_id>/<hash>@<vN>
# 索引层: 变更清单(常驻内存,轻量)
CHANGES_INDEX_DIR = HOME / "changes-index"  # <home>/changes-index/<session_id>.json
# 详情层: 单次变更的完整 diff(按需加载)
CHANGES_DETAIL_DIR = HOME / "changes-detail"  # <home>/changes-detail/<session_id>/cd_*.json


@dataclass(frozen=True)
class Settings:
    provider: str | None = None             # forced provider, else auto-detect
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_chat_model: str = "gpt-4o"
    # --- Knowledge base(资料库/RAG) embedding 配置(OpenAI 兼容 /embeddings) ---
    embedding_base_url: str = ""            # 空 = 未配置,资料库检索不可用
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_dims: int = 0                 # 0 = 首次调用时从 API 响应探测
    milvus_uri: str = ""                    # 空 = 本地 milvus-lite 文件;http(s):// = standalone
    kb_chunk_tokens: int = 700              # 分块目标 token 数
    kb_top_k: int = 5                       # 检索返回条数
    offline_script: str = ""

    @staticmethod
    def load() -> "Settings":
        get = os.environ.get
        return Settings(
            provider=get("SOUL_PROVIDER") or None,
            deepseek_api_key=get("DEEPSEEK_API_KEY", ""),
            deepseek_base_url=get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            deepseek_model=get("DEEPSEEK_MODEL", "deepseek-chat"),
            anthropic_api_key=get("ANTHROPIC_API_KEY", ""),
            anthropic_base_url=get("ANTHROPIC_BASE_URL", ""),
            anthropic_model=get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            openai_api_key=get("OPENAI_API_KEY", ""),
            openai_base_url=get("OPENAI_BASE_URL", ""),
            openai_chat_model=get("OPENAI_CHAT_MODEL", "gpt-4o"),
            embedding_base_url=get("EMBEDDING_BASE_URL", ""),
            embedding_api_key=get("EMBEDDING_API_KEY", ""),
            embedding_model=get("EMBEDDING_MODEL", ""),
            embedding_dims=int(get("EMBEDDING_DIMS") or 0),
            milvus_uri=get("MILVUS_URI", ""),
            kb_chunk_tokens=int(get("KB_CHUNK_TOKENS") or 700),
            kb_top_k=int(get("KB_TOP_K") or 5),
            offline_script=get("SOUL_OFFLINE_SCRIPT", ""),
        )


# Networking — sidecar only ever binds loopback (A09: DNS-rebind guard)
BIND_HOST = "127.0.0.1"
REQUIRED_HOSTS = {"127.0.0.1", "localhost"}
