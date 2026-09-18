"""逐步调试 Agent 主循环 —— 真实 provider 版。

一个真实请求进去，后台到底发生了什么：用**真模型**（读 .env 里的 key）跑一次
完整的 `SoulAgent.run()`，然后你可以在这个脚本里一步步单步跟。

与离线版的区别
--------------
* 不设 `SOUL_SKIP_DOTENV` → 正常加载 `.env`，于是 provider / API key 生效
* 不设沙箱 `SOUL_BUDDY_HOME` → 用你真实的 `~/.soul_buddy`
  （会真实写入会话、transcript、审计、记忆，这正是"真实走循环"的要求）
* provider 用 `select_provider(Settings.load())` → 按 `.env` 的 `SOUL_PROVIDER` 选

用法
----
IDE 里调试（推荐）：
    PyCharm:  Run → Debug 'Script'
    VS Code:  F5 → "Agent Loop (real provider)"
命令行直接跑：
    .venv\\Scripts\\python.exe script\\debug_agent_loop.py

想让它跑得更"干净"时（可选，在 Run Configuration 的环境变量里设）：
    SOUL_PROVIDER=deepseek      # 强制某个 provider
    SOUL_OFFLINE_SCRIPT=        # 留空
    SOUL_RUBRIC_MODE=advisory   # 打开 rubric 验收，顺带调那一支

推荐断点（按执行顺序，行号见注释）
--------------------------------
    agent.py:126   messages = self.storage.bootstrap_messages(...)
    agent.py:144   for turn in range(1, MAX_TURNS + 1)          ★ 轮次循环
    agent.py:150   tools_specs = self.tools.specs()
    agent.py:158   system, system_parts = self._system_prompt(...)   ★ 提示词拼装
    agent.py:212   req = ProviderRequest(...)                    ★ 发给 LLM 的载荷
    agent.py:244   self.provider.create(req)                     ★ 真实模型调用
    agent.py:309   messages.append(model_turn.raw_assistant)
    agent.py:389   for call in model_turn.tool_calls:            ★ 工具循环
    agent.py:599   decision = self.permissions.decide(req)       ★ 权限判定
    agent.py:650   self.tools.dispatch(...)                      ★ 真正执行工具
    agent.py:338   if not model_turn.wants_tools:                ★ 收尾判定
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK，中文/符号可能触发 UnicodeEncodeError 直接打断调试。
# 强制 stdout/stderr 用 UTF-8 且 errors='replace'，最坏情况是显示成 '?' 而不是崩。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# --- 代理处理 ---------------------------------------------------------------
# Electron 启动 sidecar 时会剥离全部代理环境变量并设 NO_PROXY=*
# （见 desktop/src/main/sidecar.ts:99-109），因为 Windows 上 httpx 会读系统
# 注册表里的 SOCKS 代理（常见于 Clash/v2ray），而 httpx 缺 socksio 时会直接
# 抛 ImportError 崩掉。手动跑脚本不会自动剥离，所以这里显式处理。
#
#   STRIP_PROXY = True   -> 走直连（与 Electron 行为一致）
#   STRIP_PROXY = False  -> 保留系统代理（前提是装了 socksio，即 HTTP_PROXY 可用）
# 想用 http 代理而非 socks：把 SOUL_DEBUG_PROXY 设成 http://127.0.0.1:7890
STRIP_PROXY = True
SOUL_DEBUG_PROXY = os.environ.get("SOUL_DEBUG_PROXY", "")


def _configure_proxy() -> None:
    """让 httpx 不要误用系统 SOCKS 代理。"""
    if STRIP_PROXY:
        for k in list(os.environ):
            if "proxy" in k.lower():
                del os.environ[k]
        os.environ["NO_PROXY"] = "*"
        os.environ["no_proxy"] = "*"
        # httpx 还会读 trust_env 里的系统代理，靠下面这个把它关掉最稳。
        os.environ["HTTPX_NO_ENV_PROXY"] = "1"
        print("[proxy] 已剥离系统代理（NO_PROXY=*，直连）")
    elif SOUL_DEBUG_PROXY:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                  "http_proxy", "https_proxy", "all_proxy"):
            os.environ[k] = SOUL_DEBUG_PROXY
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        os.environ["no_proxy"] = "127.0.0.1,localhost"
        print(f"[proxy] 使用显式代理 {SOUL_DEBUG_PROXY}")


_configure_proxy()

# 只决定"用哪个 provider / 哪个模型"，其余照你 .env 来。
# 改成 "anthropic" / "openai-chat" 可切换；写成 None 则按 .env 的 SOUL_PROVIDER。
PROVIDER_OVERRIDE: str | None = "deepseek"
MODEL_OVERRIDE: str | None = None      # None = 用 .env 里的模型名

# 本次任务与工作区 —— 想调别的场景就改这两行
WORKSPACE = r"C:\andy\codebase\demo"
USER_REQUEST = "在当前目录创建一个 debug_demo.txt，内容写 'hello from debug'，然后告诉我改了什么"

# 是否实时打印事件（相当于把 SSE 流搬到终端）。断点调试时可设为 False 减少噪音。
SHOW_EVENTS = True

# ---------------------------------------------------------------------------

from soul_buddy import config                                    # noqa: E402
from soul_buddy.agent import SoulAgent                           # noqa: E402
from soul_buddy.audit import AuditLog                            # noqa: E402
from soul_buddy.models import SessionRecord                      # noqa: E402
from soul_buddy.permissions import (                              # noqa: E402
    AutoApproveGate, PermissionPolicy, WorkspaceScope,
)
from soul_buddy.providers import select_provider                  # noqa: E402
from soul_buddy.providers.offline import OfflineProvider          # noqa: E402
from soul_buddy.rubric import RubricPolicy                        # noqa: E402
from soul_buddy.storage import SessionStore                       # noqa: E402
from soul_buddy.tools import build_default_registry                # noqa: E402


class _ConsoleBus:
    """把 emit 的事件实时打印 —— 相当于把 SSE 流搬到终端。

    做了三件事让日志好读：
      1. **按轮分组**：见到 final_prompt 就开一个新轮次标题，后续事件缩进
      2. **区分估算/校准**：context_usage 打印 [估算] 或 [校准] 前缀
      3. **抑制噪音**：同一轮的第二次 context_usage、以及重复的 snapshot 折叠
    """

    def __init__(self) -> None:
        self._turn: int | None = None
        self._seen_usage_in_turn = 0
        # 统计
        self.stats: dict[str, int] = {}
        self.tool_calls: list[tuple[str, bool, str]] = []   # (tool, is_error, brief)

    async def publish(self, session_id: str, event) -> None:
        etype = getattr(event, "type", "?")
        data = getattr(event, "data", {}) or {}
        name = str(etype).split(".")[-1].lower()

        self.stats[name] = self.stats.get(name, 0) + 1

        # 记录工具调用结果用于收尾汇总
        if name == "function_call_result":
            self.tool_calls.append((
                data.get("tool", "?"),
                bool(data.get("is_error")),
                (data.get("content") or "").replace("\n", " ")[:60],
            ))

        if not SHOW_EVENTS:
            return

        # 新轮次：打印分节标题
        if name == "final_prompt":
            self._turn = data.get("turn")
            self._seen_usage_in_turn = 0
            print(f"\n{'─' * 72}")
            print(f"  ▶ 第 {self._turn} 轮   "
                  f"(system {len(data.get('system') or '')} 字 / "
                  f"messages {len(data.get('messages') or [])} 条)")
            print(f"{'─' * 72}")

        # 每轮第二次 context_usage（校准值）折叠成一行提示
        if name == "context_usage":
            self._seen_usage_in_turn += 1
            tag = "[估算]" if self._seen_usage_in_turn == 1 else "[校准]"
            total = data.get("total")
            pct = data.get("pct")
            pct_s = f"{pct:.1f}%" if isinstance(pct, (int, float)) else "?"
            extra = ""
            if tag == "[估算]":
                extra = (f"  (system {data.get('system')} / tools {data.get('tools')}"
                         f" / msgs {data.get('messages')})")
            print(f"      {tag} 上下文 {total} tok · {pct_s}{extra}")
            return

        line = _brief(name, data)
        if line:
            print(f"      {line}")


def _brief(name: str, data: dict) -> str:
    """把事件载荷压成一行摘要，避免刷屏。

    用纯 ASCII 标记而非 emoji —— Windows 控制台默认 GBK，emoji 会触发
    UnicodeEncodeError 把整个调试脚本打断。
    """
    if name == "message":
        role = data.get("role", "?")
        tag = "USER " if role == "user" else "ASSIS"
        text = (data.get("text") or "").replace("\n", " ")
        return f"[{tag}] {text[:100]}"
    if name == "reasoning":
        text = (data.get("text") or "").replace("\n", " ")
        return f"[think] {text[:100]}"
    if name == "function_call":
        return f"[call ] {data.get('tool')}  args={str(data.get('arguments'))[:70]}"
    if name == "function_call_result":
        c = (data.get("content") or "").replace("\n", " ")
        flag = "ERROR" if data.get("is_error") else "ok   "
        return f"   \\-> [{flag}] {c[:70]}"
    if name == "permission_request":
        return f"[perm ] 请求授权 {data.get('tool')}  reason={data.get('reason')}"
    if name == "permission_denied":
        return f"[perm ] 已拒绝 {data.get('tool')}  {data.get('reason')}"
    if name in ("permission_resolved", "permission_expired"):
        return f"[perm ] {name}  {data.get('action', data.get('call_id', ''))}"
    if name == "file-history-snapshot":
        phase = "after " if data.get("isSnapshotUpdate") else "before"
        return f"[snap ] 快照({phase})"
    if name == "artifact_presented":
        cards = data.get("cards") or []
        return f"[art  ] 展示产出物 x{len(cards)}"
    if name in ("run_finished", "run_aborted"):
        kv = {k: v for k, v in data.items() if k != "modified_files"}
        return f"[done ] {name}  {kv}"
    if name == "turn_budget_warning":
        return f"[warn ] 轮次预算告警 {data.get('turn')}/{data.get('max')}"
    if name in ("rubric_evaluated", "rubric_passed", "rubric_failed",
                "rubric_retry", "rubric_safety_violation"):
        return f"[rubri] {name}  passed={data.get('passed')} total={data.get('total')}"
    if name in ("skill_loaded", "context_limit_exceeded", "error"):
        return f"[info ] {name}  {str(data)[:80]}"
    return ""


def build_provider():
    """按 .env 选一个真实 provider；没 key 时明确报错而不是静默降级。

    select_provider() 在"找不到任何 key"时会静默回退到 offline —— 那会让人
    以为在调真模型，实则拿到脚本化输出。这里显式拦截。
    """
    settings = config.Settings.load()
    if PROVIDER_OVERRIDE:
        settings = _with_override(settings, PROVIDER_OVERRIDE, MODEL_OVERRIDE)

    provider = select_provider(settings)
    if isinstance(provider, OfflineProvider):
        raise SystemExit(
            "没有找到可用的 API key，provider 回退到了 offline。\n"
            f"请检查 {ROOT / '.env'} 里的 DEEPSEEK_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY。"
        )
    key_field = f"{provider.name.replace('-', '_')}_api_key"
    if not getattr(settings, key_field, ""):
        raise SystemExit(f"provider={provider.name} 但 {key_field} 为空，请检查 .env")
    return provider, settings


def _with_override(settings, provider_name: str, model: str | None):
    """dataclasses.replace 不好用（frozen + 字段名映射），直接改属性副本。"""
    import dataclasses
    changes: dict = {"provider": provider_name}
    if model:
        field = {
            "deepseek": "deepseek_model",
            "anthropic": "anthropic_model",
            "openai-chat": "openai_chat_model",
        }.get(provider_name)
        if field:
            changes[field] = model
    return dataclasses.replace(settings, **changes)


async def main() -> int:
    ws = Path(WORKSPACE)
    if not ws.is_dir():
        print(f"[warn] 工作区不存在，自动创建: {ws}")
        ws.mkdir(parents=True, exist_ok=True)

    provider, settings = build_provider()
    print(f"provider     : {provider.name}  model={getattr(provider, 'model', '?')}")
    print(f"home         : {config.HOME}")
    print(f"workspace    : {ws}")
    print(f"request      : {USER_REQUEST}")
    print("=" * 76)

    storage = SessionStore()
    session = SessionRecord.create(str(ws))
    storage.save_session(session)
    print(f"session      : {session.id}")

    bus = _ConsoleBus()
    agent = SoulAgent(
        storage, build_default_registry(), bus, AuditLog(),
        provider,
        PermissionPolicy(WorkspaceScope(ws)),
        rubric=RubricPolicy.from_config(),     # 跟随 .env 的 SOUL_RUBRIC_MODE
    )

    # 权限：这里用 AutoApproveGate（自动放行 ASK）。
    # 想观察权限挂起，把它换成 PermissionGate 并手动喂选择。
    approver = AutoApproveGate()

    print("=" * 76)
    # ── ★ 在这行下断点，F7 步入 agent.run()，开始单步 ──────────────
    result = await agent.run(session, USER_REQUEST, approver)

    # ── 运行汇总（比逐行事件更适合快速复盘）────────────────────────
    print("\n" + "=" * 76)
    print("运行汇总")
    print("=" * 76)
    print(f"结论        : {'✅ 完成' if not result.truncated else '⚠️ 被截断'}"
          f"  轮次 {result.turns}  truncated={result.truncated}")
    print(f"改动文件    : {result.modified_files or '（无）'}")
    if result.rubric:
        print(f"Rubric      : passed={result.rubric.get('passed')} "
              f"total={result.rubric.get('total')} "
              f"failed={result.rubric.get('failed_gating')} "
              f"degraded={result.rubric.get('degraded')}")
    print(f"最终答复    : {result.text[:160]!r}")

    print("\n工具调用序列：")
    if not bus.tool_calls:
        print("  （无）")
    for i, (tool, is_err, brief) in enumerate(bus.tool_calls, 1):
        mark = "❌" if is_err else "✅"
        print(f"  {i:>2}. {mark} {tool:<16} {brief}")

    failures = [t for t, e, _ in bus.tool_calls if e]
    if failures:
        print(f"\n⚠️  失败调用 {len(failures)} 次: {failures}")

    print("\n事件计数：")
    for k, v in sorted(bus.stats.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<26} {v}")

    print(f"\ntranscript  : {storage._session_dir(session.id, session.workspace_root)}")
    print("             （events.jsonl 是唯一真相源，比日志完整）")

    # 会话目录里还落了最终请求载荷，方便离线复盘
    sdir = storage._session_dir(session.id, session.workspace_root)
    print(f"会话文件    : {[p.name for p in sdir.iterdir()]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
