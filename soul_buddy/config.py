"""Global configuration & constants for soul_buddy.

Single source of truth for limits, thresholds and directory layout.
All values referenced by the implementation plan (§4.1 / §5.5) and the
clarification answers (A05–A26) live here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


# --- .env bootstrap ----------------------------------------------------------
# MUST run before any constant below is evaluated.
#
# api/main.py also calls load_dotenv(), but only *after* it imports the routers
# — and those import this module. The constants below are evaluated at import
# time, so a load that happens later silently misses them. Loading here makes
# .env authoritative for everything this file defines.
def _load_env_files() -> None:
    """Load .env from the usual locations; real env vars always win.

    ``SOUL_SKIP_DOTENV=1`` disables the whole lookup — the test suite sets it
    so results never depend on the developer's local .env.
    """
    if os.environ.get("SOUL_SKIP_DOTENV"):
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # dotenv is optional at import time
        return

    candidates: list[Path] = []
    explicit = os.environ.get("SOUL_ENV_FILE")
    if explicit:
        candidates.append(Path(explicit).expanduser())
    # User-level: <home>/.env — the practical spot for a packaged app, since
    # the repo checkout is not shipped.
    raw_home = os.environ.get("SOUL_BUDDY_HOME")
    home = Path(raw_home).expanduser() if raw_home else Path.home() / ".soul_buddy"
    candidates.append(home / ".env")
    # Source checkout: <repo>/.env
    candidates.append(Path(__file__).resolve().parent.parent / ".env")
    # Whatever directory the process happened to start from.
    candidates.append(Path.cwd() / ".env")

    seen: set[str] = set()
    for path in candidates:
        try:
            key = str(path.resolve())
        except OSError:
            continue
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        # override=False: an explicit environment variable beats .env. This
        # also keeps pytest's pinned values authoritative over the file.
        load_dotenv(path, override=False)


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return raw.strip() if raw and raw.strip() else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


_load_env_files()

# --- Agent loop limits -------------------------------------------------------
MAX_TURNS = 40                      # BR-01
TURN_BUDGET_WARNING = 32            # BR-01: 80% -> emit turn_budget_warning
REPEAT_CALL_LIMIT = 3              # BR-02: same (tool, args) >= 3 -> deny (per-run scope, A02)

# --- First-turn reasoning (方案 B) ------------------------------------------
# 第一轮如果模型直接出 tool_calls 且 text 长度 < 该阈值,强制它先输出推理再调工具。
FIRST_TURN_REASONING_MIN_LEN = 20    # 字符数,低于此值视为"没有推理"
FIRST_TURN_REASONING_MAX_RETRIES = 2  # 最多强制重试几次,超过则放行(避免死循环)

# --- Context window per model (A13) ------------------------------------------
# 键是「模型名」而不是平台 / provider 名：一个平台会挂多个模型，窗口各不相同
# （例如 DeepSeek 的 deepseek-flash 是 1M，而 Qwen3-8B 只有 32k）。
# 查表统一走 context_window(model)；未收录的模型回落到 DEFAULT_CONTEXT_WINDOW。
DEFAULT_CONTEXT_WINDOW = 32_000

CONTEXT_WINDOW: dict[str, int] = {
    # --- DeepSeek：在售的官方 model id，都是 1M ---
    # deepseek-flash   = DeepSeek-V4.1-Flash（支持图像理解；思考/非思考可切）
    # deepseek-v4-pro  = DeepSeek-V4-Pro-0813（不支持图像理解）
    "deepseek-flash": 1_000_000,
    "deepseek-v4-pro": 1_000_000,
    # 旧名 / 非官方写法。deepseek-v4-flash 仍可调用（模型已下线，路由到
    # V4.1-Flash 按 Flash 计费）；deepseek-chat / deepseek-reasoner 已于
    # 2026-07-24 停用。保留只为让存量 env 拿到正确窗口，不回落到 32k。
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4.1-flash": 1_000_000,
    "deepseek-chat": 1_000_000,
    "deepseek-reasoner": 1_000_000,
    # --- Anthropic ---
    "claude-sonnet-4-20250514": 200_000,
    "claude-sonnet-4": 200_000,
    # --- OpenAI ---
    "gpt-4o": 128_000,
    # --- 硅基流动（Qwen3-8B）：原生 32,768 tokens，取 32k 留一点余量 ---
    "Qwen/Qwen3-8B": 32_000,
    # --- offline（无真实模型，脚本化多轮）---
    "offline": 8_000,
}
COMPACT_TRIGGER_RATIO = 0.75
COMPACT_TARGET_RATIO = 0.50
RESERVE_FOR_OUTPUT = 4_096
SUMMARY_INPUT_MAX_CHARS = 30_000    # dropped-history text sent to the summarizer (tail kept)
SUBAGENT_KEEP_RECENT_TURNS = 4      # sub-agents are ephemeral workers: smaller history floor


def context_window(model: str | None) -> int:
    """按模型名解析上下文窗口（token）。

    1. 精确命中 CONTEXT_WINDOW 直接用；
    2. 否则按前缀匹配（最长键优先），兼容 "deepseek-chat-0324" 这类带后缀的 id；
    3. 都没命中（含空值）回落到 DEFAULT_CONTEXT_WINDOW，不替未知模型猜窗口。
    """
    if not model:
        return DEFAULT_CONTEXT_WINDOW
    if model in CONTEXT_WINDOW:
        return CONTEXT_WINDOW[model]
    best = ""
    for key in CONTEXT_WINDOW:
        if len(key) > len(best) and model.startswith(key):
            best = key
    return CONTEXT_WINDOW[best] if best else DEFAULT_CONTEXT_WINDOW

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

# --- Runtime rubric self-verification (P6 / M16) ----------------------------
# See docs/rubric-design.md and docs/modules/20-rubric.md.
# All seven knobs are settable from the environment or from .env (.env.example
# lists them). An explicit environment variable always beats the file.
#   off      - never enters the verification stage (default: byte-identical to
#              the pre-rubric behaviour, see INV-21)
#   advisory - scores + persists + emits, but never retries (calibration mode)
#   enforce  - scores and retries the model when the report does not pass
RUBRIC_MODE = _env_str("SOUL_RUBRIC_MODE", "off").lower()
RUBRIC_PASS_THRESHOLD = _env_int("SOUL_RUBRIC_PASS_THRESHOLD", 70)       # 0-100
RUBRIC_MAX_RETRIES = _env_int("SOUL_RUBRIC_MAX_RETRIES", 1)              # INV-17
RUBRIC_MIN_TURNS_LEFT = _env_int("SOUL_RUBRIC_MIN_TURNS_LEFT", 3)
RUBRIC_LLM_JUDGE = _env_bool("SOUL_RUBRIC_LLM_JUDGE", True)             # Q5/Q6
RUBRIC_JUDGE_DIFF_MAX_CHARS = _env_int("SOUL_RUBRIC_JUDGE_DIFF_MAX_CHARS", 8000)
RUBRIC_DEGRADE_ON_NO_PROVIDER = _env_bool("SOUL_RUBRIC_DEGRADE_ON_NO_PROVIDER", True)
# 用哪个 provider 跑 Q5/Q6 的 LLM judge —— 让裁判模型与会话模型解耦：
# 换会话模型只改变"被评的对象"，不会连带换掉裁判，报告之间才可比。
#   空 / session / self / auto = 复用会话自己的 provider（旧行为）
#   其它值（默认 xiaomi）= 用该 provider；未配置对应 key 时自动回落到会话 provider
RUBRIC_JUDGE_PROVIDER = _env_str("SOUL_RUBRIC_JUDGE_PROVIDER", "xiaomi").lower()

# --- Rubric judge backend (Q5/Q6) -------------------------------------------
#   llm      - default: ask the provider to emit {"scores": [...]} and parse it.
#   typesafe - ask api.typesafe.ai for a calibrated probability distribution
#              over the same anchor table, plus a `confidence`.
# Switching backends changes *how* Q5/Q6 are measured, never the dimension list
# or the anchor tables — those live in rubric/policy.py.
RUBRIC_JUDGE_BACKEND = _env_str("SOUL_RUBRIC_JUDGE_BACKEND", "llm").lower()
# Read once at import. Never returned to the client, never written to a report.
TYPESAFE_API_KEY = (os.environ.get("TYPESAFE_API_KEY") or "").strip()
RUBRIC_TYPESAFE_MODEL = _env_str("SOUL_RUBRIC_TYPESAFE_MODEL", "jev-latest")
RUBRIC_TYPESAFE_TIMEOUT = _env_float("SOUL_RUBRIC_TYPESAFE_TIMEOUT", 30.0)
# Upper bound (seconds) on a single *provider* judge call (backend=llm).
# A "thinking" model burns hundreds of tokens of reasoning before emitting the
# JSON: measured 41s and 209s on Qwen/Qwen3-8B vs 2-5s on deepseek-chat, and the
# OpenAI SDK's own default is a 600s timeout plus two retries. An unbounded call
# holds back the final message and run_finished, so the UI sits on "运行中" long
# after the answer is on screen. Timing out degrades to the rule layer only
# (INV-20) instead of stalling the run. 0 disables the bound.
RUBRIC_JUDGE_TIMEOUT = _env_float("SOUL_RUBRIC_JUDGE_TIMEOUT", 30.0)
# Output budget for one judge call. The judge asks a thinking model to skip its
# reasoning (Provider.thinking_off_extra_body), so a few dozen tokens normally
# suffice — this is a safety net for models that ignore that switch. Measured on
# xiaomi/mimo-v2.5 with thinking ON: 700 tokens went entirely to
# `reasoning_content` and `content` came back EMPTY, which the strict parser
# rejects, silently degrading Q5/Q6 to "not applicable".
RUBRIC_JUDGE_MAX_TOKENS = _env_int("SOUL_RUBRIC_JUDGE_MAX_TOKENS", 3000)
# Below this the judgement is reported as not-applicable instead of being
# averaged in. 0.5 is a *conservative floor*, not a discriminator: measured over
# 6 repeats per scenario (spike/typesafe_confidence_dist.py) this judge runs at
# 0.85-0.94 on clean material and still 0.55-0.72 on deliberately vague
# material, so the floor only fires once the distribution has essentially
# collapsed. Raise it to trade coverage for strictness.
RUBRIC_TYPESAFE_MIN_CONFIDENCE = _env_float(
    "SOUL_RUBRIC_TYPESAFE_MIN_CONFIDENCE", 0.5)
# When TypeSafe is unreachable, fall back to the provider judge rather than
# losing the LLM dimensions entirely (INV-20: the rubric must never become an
# availability single point of failure).
RUBRIC_TYPESAFE_FALLBACK_TO_LLM = _env_bool(
    "SOUL_RUBRIC_TYPESAFE_FALLBACK_TO_LLM", True)

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
LOG_LEVEL = _env_str("SOUL_LOG_LEVEL", "INFO")

# --- LangSmith tracing (observability) --------------------------------------
# Tracing is driven by the langsmith SDK's own env vars; these constants exist
# so the sidecar can report the *effective* configuration to the UI without the
# frontend ever seeing the API key. `LANGSMITH_TRACING` gates everything: with
# it false the wrap_openai / wrap_anthropic shims are pass-through no-ops.
LANGSMITH_TRACING = _env_bool("LANGSMITH_TRACING", False)
LANGSMITH_PROJECT = _env_str("LANGSMITH_PROJECT", "soul-buddy")
LANGSMITH_ENDPOINT = _env_str("LANGSMITH_ENDPOINT",
                              "https://api.smith.langchain.com")
# The API key itself is never returned to the client — only whether it is set.
LANGSMITH_API_KEY_SET = bool((os.environ.get("LANGSMITH_API_KEY") or "").strip())
# Console URL for a project's trace list (workspace-agnostic default).
LANGSMITH_BASE_URL = _env_str("LANGSMITH_BASE_URL", "https://smith.langchain.com")


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
    # 官方当前 model id 是 deepseek-flash（= DeepSeek-V4.1-Flash，1M 上下文）。
    # 旧名 deepseek-v4-flash 仍可调用但模型已下线（路由到 V4.1-Flash 计费）；
    # deepseek-chat / deepseek-reasoner 已于 2026-07-24 停用。
    deepseek_model: str = "deepseek-flash"
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_chat_model: str = "gpt-4o"
    # --- 硅基流动（SiliconFlow，OpenAI 兼容）:Qwen3-8B ---
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_model: str = "Qwen/Qwen3-8B"
    # --- 小米 MiMo（OpenAI 兼容；rubric judge 的默认模型）---
    xiaomi_api_key: str = ""
    xiaomi_base_url: str = "https://api.xiaomimimo.com/v1"
    xiaomi_model: str = "mimo-v2.5"
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
            deepseek_model=get("DEEPSEEK_MODEL", "deepseek-flash"),
            anthropic_api_key=get("ANTHROPIC_API_KEY", ""),
            anthropic_base_url=get("ANTHROPIC_BASE_URL", ""),
            anthropic_model=get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            openai_api_key=get("OPENAI_API_KEY", ""),
            openai_base_url=get("OPENAI_BASE_URL", ""),
            openai_chat_model=get("OPENAI_CHAT_MODEL", "gpt-4o"),
            siliconflow_api_key=get("SILICONFLOW_API_KEY", ""),
            siliconflow_base_url=get("SILICONFLOW_BASE_URL",
                                     "https://api.siliconflow.cn/v1"),
            siliconflow_model=get("SILICONFLOW_MODEL", "Qwen/Qwen3-8B"),
            xiaomi_api_key=get("XIAOMI_API_KEY", ""),
            xiaomi_base_url=get("XIAOMI_BASE_URL",
                                "https://api.xiaomimimo.com/v1"),
            xiaomi_model=get("XIAOMI_MODEL", "mimo-v2.5"),
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
