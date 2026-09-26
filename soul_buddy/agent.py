"""SoulAgent — the real LLM tool-calling loop (replaces mini_workbuddy._plan).

Design invariants (BR-01/02/18/19, A11):
  * MAX_TURNS cap; a budget warning is emitted at turn 32 (80%).
  * Same (tool, args) repeated >= 3 times in a single run -> converted to a deny
    (loop protection). Scope is per-run (A02).
  * A DENY never terminates the loop — it is returned to the model as tool output
    so it can try another approach (BR-18).
  * Tool exceptions are converted to ToolResult data, never crash the loop (BR-19).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import Counter
from typing import Optional

import anyio

log = logging.getLogger("soul_buddy.agent")

# LangSmith @traceable: creates a top-level trace for each agent run.
# All LLM calls inside (auto-traced by wrap_openai / wrap_anthropic)
# become child runs, forming a complete nested trace tree per invocation.
# Tracing only emits when LANGSMITH_TRACING=true; otherwise no-op.
try:
    from langsmith import traceable as _ls_traceable
except ImportError:  # pragma: no cover - langsmith is an optional dep at runtime
    def _ls_traceable(*_a, **_kw):
        def _wrap(fn): return fn
        return _wrap

from .config import (
    FIRST_TURN_REASONING_MAX_RETRIES, FIRST_TURN_REASONING_MIN_LEN,
    MAX_TURNS, REPEAT_CALL_LIMIT, TURN_BUDGET_WARNING,
)
from .context.tokens import estimate_messages, estimate_tokens
from .memory.pricing import price
from .models import (
    Event, EventType, RunResult, SessionRecord, ToolResult, new_id,
)
from .permissions import (
    PermissionAction, PermissionPolicy, PermissionRequest, READ_TOOLS, WRITE_TOOLS,
)
from .prompts import get_system_prompt
from .providers.base import (ModelTurn, Provider, ProviderRequest, ToolCall,
                             sanitize_tool_messages, with_file_refs)
from .skills.registry import authorize_skill_tool
from . import rubric as _rubric


def _model_label(provider) -> str:
    """Human-readable model id for provenance, falling back to the provider name.

    ``Provider.model`` is empty for providers without a real model (offline);
    the provider name is the next best label so a report never loses its
    "who ran this" origin entirely.
    """
    if provider is None:
        return ""
    return getattr(provider, "model", "") or getattr(provider, "name", "") or ""


class SoulAgent:
    def __init__(self, storage, tools, events, audit, provider: Provider,
                 permissions: PermissionPolicy, context=None, memory=None,
                 skills=None, stream: bool = False,
                 subagents=None, subagent_runner_factory=None,
                 expert=None, kb_summary: str | None = None,
                 knowledge=None, kb_ids: list[str] | None = None,
                 rubric=None, rubric_provider: Provider | None = None) -> None:
        self.storage = storage
        self.tools = tools
        self.events = events
        self.audit = audit
        self.provider = provider
        self.permissions = permissions
        self.context = context
        self.memory = memory
        self.skills = skills              # P5: SkillRegistry (lazy skill index)
        self.stream = stream              # P5: emit assistant_delta SSE events
        self.subagents = subagents        # SubAgentRegistry (None = disabled)
        self.subagent_runner_factory = subagent_runner_factory  # callable() -> SubAgentRunner
        self.expert = expert              # s18: Expert 包 (None = 普通会话)
        self.kb_summary = kb_summary      # 专家绑定资料库的说明(kb_ids 非空才有)
        self.knowledge = knowledge        # KnowledgeRetriever (None = 检索不可用)
        self.kb_ids = kb_ids or []        # 会话可检索的资料库 id 列表
        # P6: runtime rubric self-verification. Defaults to the config-derived
        # policy, whose mode defaults to "off" — so wiring this in changes
        # nothing until someone opts in (INV-21).
        self.rubric = (rubric if rubric is not None
                       else _rubric.RubricPolicy.from_config())
        # P6: the judge may be pinned to a dedicated model
        # (SOUL_RUBRIC_JUDGE_PROVIDER). None = reuse the session provider, which
        # is what happens whenever no dedicated judge model is configured.
        self.rubric_provider = rubric_provider
        self._call_counter: Counter[tuple[str, str]] = Counter()
        # Exposed so an abort can report partial progress (BR: no silent loss).
        self.modified_files: list[str] = []
        self.turns_used: int = 0

    # --- event helpers ------------------------------------------------------
    async def _aemit(self, session: SessionRecord, etype: str, data: dict) -> None:
        ev = self.storage.append_event(session.id, etype, data)
        await self.events.publish(session.id, ev)

    async def _apublish_delta(self, session: SessionRecord, text: str) -> None:
        """P5: publish a streamed chunk without persisting it (sequence=0)."""
        ev = Event(session_id=session.id, sequence=0,
                   type=EventType.ASSISTANT_DELTA, data={"text": text})
        await self.events.publish(session.id, ev)

    async def _apublish_reasoning_delta(self, session: SessionRecord,
                                        text: str) -> None:
        """Publish a streamed reasoning chunk without persisting it (sequence=0).

        The complete reasoning is persisted afterwards as a REASONING event.
        """
        ev = Event(session_id=session.id, sequence=0,
                   type=EventType.REASONING_DELTA, data={"text": text})
        await self.events.publish(session.id, ev)

    # --- main entry ---------------------------------------------------------
    @_ls_traceable(name="SoulAgent.run", run_type="chain")
    async def run(self, session: SessionRecord, text: str,
                  approver, images: list[dict] | None = None,
                  files: list[dict] | None = None) -> RunResult:
        """Run one user request.

        `images` (optional) is a list of already-persisted upload references:
        {"path", "media_type", "name", "size", "file"}. They ride along in the
        first user message as file-ref blocks; providers resolve them to
        native image blocks at wire time.

        `files` (optional) is the same contract for text attachments:
        {"type": "file", "path", "name", "mime", "size", "file"} refs. Content
        is read from disk only when the request goes on the wire.
        """
        self._call_counter.clear()
        self.modified_files = []           # shared ref: visible to abort
        modified_files = self.modified_files
        # Reset per-run gate shortcuts (deny_rest / allow_rest) from previous run.
        reset = getattr(approver, "reset_run_flags", None)
        if callable(reset):
            reset()
        self.turns_used = 0
        self.rubric.reset_run()            # P6: retry budget is per-run (INV-16)
        log.info("run start session=%s provider=%s prompt_len=%d",
                 session.id, self.provider.name, len(text))

        # request_id 贯穿单次用户请求,用于串联 jsonl 事件、change、回滚指针
        request_id = new_id()
        messages = self.storage.bootstrap_messages(session, self.provider)
        messages.append(with_file_refs(
            self.provider.initial_user_message(text, images), files))
        user_event: dict = {"role": "user", "text": text, "request_id": request_id}
        if images:
            user_event["images"] = [
                {"file": im.get("file", ""), "name": im.get("name", ""),
                 "mime": im.get("media_type", ""), "size": im.get("size", 0)}
                for im in images]
        if files:
            user_event["files"] = [
                {"file": f.get("file", ""), "name": f.get("name", ""),
                 "mime": f.get("mime", ""), "size": f.get("size", 0)}
                for f in files]
        await self._aemit(session, EventType.MESSAGE, user_event)
        await self._aemit(session, EventType.RUN_STARTED,
                          {"session_id": session.id, "request_id": request_id,
                           "started_at": time.time()})
        # P5: auto-load a skill whose read_when trigger matches the request,
        # BEFORE the system prompt is assembled so its body is in context.
        if self.skills is not None:
            auto = self.skills.match(text)
            if auto:
                self.skills.load(auto)
                await self._aemit(session, EventType.SKILL_LOADED,
                                  {"title": auto, "auto": True})
        # 方案 B: first-turn reasoning guard — 计数已强制推理的次数
        first_turn_reasoning_retries = 0

        for turn in range(1, MAX_TURNS + 1):
            self.turns_used = turn
            if turn == TURN_BUDGET_WARNING:
                await self._aemit(session, EventType.TURN_BUDGET_WARNING,
                                  {"turn": turn, "max": MAX_TURNS})

            tools_specs = self.tools.specs()
            # search_knowledge 只对「专家绑定了资料库且检索链路可用」的会话暴露;
            # 其余会话不看到该工具,handler 里的校验只是兜底。
            if self.knowledge is None or not self.kb_ids:
                tools_specs = [s for s in tools_specs
                               if s.name != "search_knowledge"]
            # system prompt 每轮重组:压缩流程在轮间提取的 durable 事实块、
            # 中途 use_skill 加载的技能内容,都要能进入后续轮次的请求 (P1-7)。
            system, system_parts = self._system_prompt(session)

            # 图片在 buffer 里只是轻引用，wire 阶段才展开成 base64 图片（按分辨率
            # 计费），纯文本估算看不到它们 —— 单独算出来给压缩阈值和用量统计。
            ref_tokens = self._wire_ref_tokens(messages)

            if self.context is not None:
                # P0-3: 触发阈值计入 system + tools + 图片 ref 开销,否则 skills/MCP
                # 块或几张截图就能让真实请求先于 messages 阈值撞上窗口。
                overhead = self._fixed_overhead(system, tools_specs) + ref_tokens
                # L4 摘要内嵌一次 provider 调用,放到线程池避免阻塞 SSE
                # 事件循环(与工具派发同理由);compact 本身绝不 raise。
                await anyio.to_thread.run_sync(
                    self.context.compact_if_needed,
                    messages, self._model_key, overhead)
                # P0-4: 硬上限预检 —— 明知会超窗的请求不发出去。
                over = None
                try:
                    over = self.context.check_hard_limit(
                        messages, self._model_key, overhead)
                    if over:
                        log.warning(
                            "turn %d over hard limit (%s), forcing reduce",
                            turn, over)
                        self.context.force_reduce(messages)
                        over = self.context.check_hard_limit(
                            messages, self._model_key, overhead)
                except Exception:
                    log.exception("hard-limit preflight failed (non-fatal)")
                if over:
                    abort_text = ("(上下文超出模型窗口上限，已停止。"
                                  "请开新会话或减少上下文后重试)")
                    await self._aemit(session, EventType.CONTEXT_LIMIT_EXCEEDED, over)
                    # 落一条消息进 transcript,聊天流里能看到终止原因
                    await self._aemit(session, EventType.MESSAGE, {
                        "role": "assistant", "text": abort_text,
                    })
                    await self._aemit(session, EventType.RUN_ABORTED, {
                        "reason": "context_limit_exceeded",
                        "modified_files": modified_files,
                        "turns": turn, **over,
                    })
                    return RunResult(
                        text=abort_text,
                        turns=turn, truncated=True,
                        reason="context_limit_exceeded",
                        modified_files=modified_files)

            # ★ 旁路: 估算 + emit —— 任何异常都吞掉, 绝不 raise
            usage = None
            try:
                from .context.usage import ContextUsageCalculator
                calc = ContextUsageCalculator(self._model_key)
                usage = calc.calc(system, system_parts, messages, tools_specs,
                                  ref_tokens)
                await self._aemit(session, EventType.CONTEXT_USAGE, usage.to_dict())
            except Exception:
                log.exception("context usage estimate failed (non-fatal)")

            req = ProviderRequest(system, messages, tools_specs, turn=turn)
            # Defense-in-depth: ensure no assistant message has tool_calls
            # without matching tool results. The provider API rejects this
            # with 400 "insufficient tool messages following tool_calls".
            sanitize_tool_messages(messages)
            # ★ 旁路: 本轮实际发给 LLM 的最终拼接提示词落进 transcript
            # (type=final_prompt, 调试/审计用)。sanitize 之后快照, 保证与
            # 真实请求一致; bootstrap_messages 不映射该事件, 回放不受影响。
            try:
                await self._aemit(session, EventType.FINAL_PROMPT, {
                    "turn": turn,
                    "provider": self.provider.name,
                    "system": system,
                    "messages": list(messages),
                })
            except Exception:
                log.exception("final_prompt emit failed (non-fatal)")
            log.debug("turn %d: calling provider=%s message_count=%d",
                      turn, self.provider.name, len(messages))
            try:
                if self.stream:
                    async def _on_delta(text: str) -> None:
                        if text:
                            await self._apublish_delta(session, text)

                    async def _on_reasoning_delta(text: str) -> None:
                        if text:
                            await self._apublish_reasoning_delta(session, text)

                    model_turn: ModelTurn = await self.provider.astream(
                        req, _on_delta, _on_reasoning_delta)
                else:
                    model_turn: ModelTurn = await anyio.to_thread.run_sync(
                        self.provider.create, req)
            except Exception:
                log.exception("provider call FAILED turn=%d", turn)
                raise
            log.info("turn %d: provider returned text_len=%d wants_tools=%s tool_calls=%d",
                     turn, len(model_turn.text), model_turn.wants_tools,
                     len(model_turn.tool_calls))

            # ★ 旁路: 校准 + emit —— 用官方 prompt_tokens 按比例缩放各 category
            if usage is not None:
                try:
                    if model_turn.usage and not model_turn.usage.get("estimated"):
                        from .context.usage import ContextUsageCalculator
                        calc = ContextUsageCalculator(self._model_key)
                        calibrated = calc.calibrate(
                            usage, model_turn.usage["prompt_tokens"])
                        await self._aemit(session, EventType.CONTEXT_USAGE,
                                          calibrated.to_dict())
                except Exception:
                    log.exception("context usage calibrate failed (non-fatal)")

            # 方案 B: first-turn reasoning guard
            # 第一轮如果模型出 tool_calls,强制它先输出有意义的推理:
            # - 长度 >= FIRST_TURN_REASONING_MIN_LEN
            # - 内容包含任务分类(简单/复杂)或 sub-agent 委托判断
            # 不满足条件则不 append raw_assistant、不 emit 工具调用,
            # 注入引导消息后重试。
            if (turn == 1
                    and model_turn.wants_tools
                    and first_turn_reasoning_retries < FIRST_TURN_REASONING_MAX_RETRIES):
                reasoning_ok = self._check_first_turn_reasoning(model_turn.text)
                if not reasoning_ok:
                    first_turn_reasoning_retries += 1
                    log.info(
                        "turn %d: forcing first-turn reasoning "
                        "(text_len=%d, retry=%d/%d)",
                        turn, len(model_turn.text.strip()),
                        first_turn_reasoning_retries,
                        FIRST_TURN_REASONING_MAX_RETRIES)
                    messages.append({
                        "role": "user",
                        "content": (
                            "在调用任何工具之前,请先用文字分析这个任务:"
                            "1) 这是什么类型的任务(简单单步 / 复杂多步)?"
                            "2) 你打算怎么做(列出步骤)?"
                            "3) 是否需要委托 sub-agent? "
                            "如果是复杂任务(需要 3+ 次工具调用),请明确说"
                            "'我将使用 task 工具委托 explore sub-agent'。"
                            "请先给出你的分析和计划,然后再调用工具。"
                        ),
                    })
                    # 不 append raw_assistant,不执行工具,直接进入下一轮
                    continue

            # 如果模型调了工具但没给任何有意义的文字解释,注入一段自然语言说明。
            # 这解决了"第一步直接调工具,用户看不到思考"的体验问题。
            # 注意:只改 emit 给前端的 text,不改 raw_assistant(保持 LLM 原样输出)。
            emit_text = model_turn.text
            if model_turn.wants_tools and not emit_text.strip():
                tool_desc = self._summarize_tool_calls(model_turn.tool_calls)
                emit_text = tool_desc + " ..."
                log.info("turn %d: injected tool-call summary (model gave no text)",
                         turn)

            messages.append(model_turn.raw_assistant)
            # A22: record model usage into the SQLite index (if memory is wired).
            self._record_usage(session, system, messages, model_turn)

            # reasoning: 先于 message emit,不映射到 LLM messages[](metadata)
            if model_turn.reasoning:
                await self._aemit(session, EventType.REASONING, {
                    "text": model_turn.reasoning,
                    "provider": self.provider.name,
                })

            # assistant message: 只含文本,工具调用单独 emit function_call
            # P6: 当 rubric 开启且本轮是收尾轮时,这段文本还只是"草稿" ——
            # 验收通过之前不能 emit,否则用户会先看到"我做完了",紧接着又看到
            # agent 继续干活。off 模式下 hold_draft 恒为 False(INV-21)。
            hold_draft = (not model_turn.wants_tools) and self.rubric.enabled
            if not hold_draft:
                await self._aemit(session, EventType.MESSAGE, {
                    "role": "assistant",
                    "text": emit_text,
                })
            # 逐个 emit 工具调用,与 LLM 的 tool_use block 一一对应
            for call in model_turn.tool_calls:
                await self._aemit(session, EventType.FUNCTION_CALL, {
                    "call_id": call.id,
                    "tool": call.name,
                    "arguments": call.arguments,
                })

            if not model_turn.wants_tools:
                # P6 verification stage. With the rubric off this is the exact
                # original path — same events, same result shape (INV-21).
                if not self.rubric.enabled:
                    await self._aemit(session, EventType.RUN_FINISHED,
                                      {"turns": turn, "truncated": False})
                    return RunResult(text=model_turn.text, turns=turn,
                                     modified_files=modified_files)

                report = await self._verify_rubric(
                    session, request_id=request_id, text=model_turn.text,
                    turn=turn, modified_files=modified_files)

                if report.safety_violation:
                    # INV-19: never retry a safety violation — retrying means
                    # inviting the model to attempt the dangerous action again.
                    await self._aemit(session, EventType.RUBRIC_SAFETY_VIOLATION,
                                      report.to_dict())
                    return await self._deliver(session, model_turn.text, turn,
                                               report, modified_files)

                retrying = (self.rubric.enforcing and not report.passed
                            and self.rubric.can_retry(turn))
                if not retrying:
                    await self._aemit(
                        session,
                        EventType.RUBRIC_PASSED if report.passed
                        else EventType.RUBRIC_FAILED,
                        report.to_dict())
                    return await self._deliver(session, model_turn.text, turn,
                                               report, modified_files)

                # Not good enough. The draft is held back — it never reaches
                # the user as a final answer — and the feedback goes back to the
                # model. raw_assistant must still be appended: dropping a valid
                # terminating assistant turn would break tool_use/tool_result
                # pairing and role alternation (C-02 / INV-18).
                messages.append(model_turn.raw_assistant)
                messages.append({"role": "user",
                                 "content": _rubric.render_feedback(report)})
                self.rubric.note_retry()
                await self._aemit(session, EventType.RUBRIC_RETRY, {
                    "retry": self.rubric.retries_used,
                    "failed": report.failed_gating,
                    "total": report.total,
                })
                log.info("rubric retry %d/%d total=%d failed=%s",
                         self.rubric.retries_used, self.rubric.max_retries,
                         report.total, report.failed_gating or "-")
                continue

            for call in model_turn.tool_calls:
                is_write = call.name in WRITE_TOOLS
                # 改前基线: emit file-history-snapshot(isSnapshotUpdate=false)
                if is_write:
                    await self._aemit(session, EventType.FILE_HISTORY_SNAPSHOT, {
                        "id": new_id(),
                        "timestamp": int(time.time() * 1000),
                        "isSnapshotUpdate": False,
                        "snapshot": {
                            "messageId": call.id,
                            "trackedFileBackups": {},
                            "cwd": session.cwd,
                        },
                    })

                # Defensive: every tool call MUST produce a result message,
                # otherwise the next provider call fails with
                # "insufficient tool messages following tool_calls message".
                _t0 = time.time()
                try:
                    result = await self._execute_governed(call, session, approver)
                except Exception as exc:
                    log.exception("tool %s raised uncaught exception", call.name)
                    from ..models import ToolResult as _TR
                    result = _TR(
                        content=f"Error: {call.name} 执行异常: {exc}",
                        is_error=True)
                _elapsed = time.time() - _t0
                if result.is_error:
                    log.warning("tool %s error after %.1fs: %s",
                                call.name, _elapsed,
                                result.content[:200].replace("\n", " "))
                elif _elapsed > 30:
                    log.info("tool %s slow: %.1fs", call.name, _elapsed)
                else:
                    log.debug("tool %s ok in %.1fs", call.name, _elapsed)

                # 改后: emit file-history-snapshot(isSnapshotUpdate=true) + 更新索引
                if is_write and not result.is_error:
                    backup = result.metadata.get("file_backup")
                    path = call.arguments.get("path", "")
                    rel = path
                    tracked = {}
                    if backup:
                        rel = backup.get("filePath", path)
                        tracked[path] = {
                            "version": backup["version"],
                            "backupTime": backup["backupTime"],
                            "backupFileName": backup["backupFileName"],
                            "hash": backup["hash"],
                            "filePath": backup["filePath"],
                        }
                    await self._aemit(session, EventType.FILE_HISTORY_SNAPSHOT, {
                        "id": new_id(),
                        "timestamp": int(time.time() * 1000),
                        "isSnapshotUpdate": True,
                        "snapshot": {
                            "messageId": call.id,
                            "trackedFileBackups": tracked,
                            "cwd": session.cwd,
                        },
                    })
                    # 生成 diff + 更新 changes-index/detail + 回滚指针
                    if hasattr(self.storage, "file_history") and self.storage.file_history:
                        try:
                            self.storage.file_history.append_change(
                                session.id, request_id, call, result, backup)
                        except Exception:
                            log.exception("append_change failed for %s", call.name)

                if call.name in WRITE_TOOLS and not result.is_error:
                    p = call.arguments.get("path")
                    if p and p not in modified_files:
                        modified_files.append(p)
                messages.extend(
                    self.provider.format_tool_results([(call, result.content)]))
                await self._aemit(session, EventType.FUNCTION_CALL_RESULT, {
                    "tool": call.name, "call_id": call.id,
                    "content": result.content,
                    "is_error": result.is_error,
                })
                # present_files -> also emit ARTIFACT_PRESENTED for the frontend
                # so the chat can render deliverable cards and the preview can auto-open.
                if call.name == "present_files" and not result.is_error:
                    import json as _j
                    try:
                        payload = _j.loads(result.content)
                    except Exception:
                        payload = {"cards": []}
                    cards = payload.get("cards", [])
                    if cards:
                        await self._aemit(session, EventType.ARTIFACT_PRESENTED, {
                            "call_id": call.id,
                            "files": call.arguments.get("files", []),
                            "cards": cards,
                        })

        await self._aemit(session, EventType.RUN_ABORTED, {
            "reason": "max_turns", "modified_files": modified_files, "turns": MAX_TURNS,
        })
        return RunResult(text="(已达轮次上限，已停止)", turns=MAX_TURNS,
                         truncated=True, reason="max_turns",
                         modified_files=modified_files)

    # --- P6: rubric verification stage --------------------------------------
    async def _verify_rubric(self, session: SessionRecord, *, request_id: str,
                             text: str, turn: int,
                             modified_files: list[str]):
        """Score this run against the rubric, persist and emit the report."""
        # The referee can be pinned to a dedicated model so that swapping the
        # session model does not also swap the judge (comparable reports).
        judge = self.rubric_provider if self.rubric_provider is not None \
            else self.provider
        try:
            events = self.storage.read_transcript(session.id)
            run_events = _rubric.slice_run(events, request_id)
            sig = _rubric.from_events(
                run_events, final_text=text, turns_used=turn,
                max_turns=MAX_TURNS,
                modified_files=list(modified_files),
                diff_text=self._collect_diff(session, request_id))
            report = await _rubric.evaluate(
                sig, provider=judge, policy=self.rubric)
        except Exception:
            # INV-20: verification must never take the run down — and a failure
            # here must not punish the model, so the run is treated as passed.
            log.exception("rubric evaluation failed (non-fatal, treated as pass)")
            report = _rubric.aggregate([], [], policy=self.rubric,
                                       detail="rubric 评估异常，已跳过")
            report.passed = True

        # Provenance for the per-model dashboard: who was graded, and by whom.
        report.model = _model_label(self.provider)
        report.judge_model = _model_label(judge)

        self._persist_report(session, request_id, report)
        await self._aemit(session, EventType.RUBRIC_EVALUATED, report.to_dict())
        log.info(report.summary_line())
        return report

    async def _deliver(self, session: SessionRecord, text: str, turn: int,
                       report, modified_files: list[str]) -> RunResult:
        """Emit the accepted answer (the draft that was held back) and finish."""
        await self._aemit(session, EventType.MESSAGE,
                          {"role": "assistant", "text": text})
        await self._aemit(session, EventType.RUN_FINISHED, {
            "turns": turn, "truncated": False,
            "rubric_passed": report.passed,
        })
        return RunResult(text=text, turns=turn,
                         modified_files=list(modified_files),
                         rubric=report.to_dict())

    def _persist_report(self, session: SessionRecord, request_id: str,
                        report) -> None:
        """Drop the report beside the session so it can be aggregated offline."""
        try:
            sdir = self.storage._session_dir(session.id, session.workspace_root)
            out = sdir / "rubric"
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{request_id}.json").write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception:
            log.exception("persist rubric report failed (non-fatal)")

    def _collect_diff(self, session: SessionRecord, request_id: str) -> str:
        """Concatenate this run's diffs for the LLM judge.

        Only the hunks matter — the judge needs to see *what changed*, not the
        whole transcript.
        """
        fh = getattr(self.storage, "file_history", None)
        if fh is None:
            return ""
        try:
            idx = fh.read_changes_index(session.id)
        except Exception:
            log.exception("read_changes_index failed (non-fatal)")
            return ""

        chunks: list[str] = []
        for entry in idx.get("changes", []):
            if entry.get("requestId") != request_id:
                continue
            detail = fh.read_change_detail(
                session.id, request_id, entry.get("revision", 1))
            if not detail:
                continue
            for fc in (detail.get("change") or {}).get("files", []):
                chunks.append(f"--- {fc.get('filePath', '')} "
                              f"({fc.get('action', '')})")
                for hunk in fc.get("hunks", []):
                    chunks.append(f"@@ -{hunk.get('oldStart')} "
                                  f"+{hunk.get('newStart')} @@")
                    chunks.extend(hunk.get("lines", []))
        return "\n".join(chunks)

    # --- governed execution -------------------------------------------------
    async def _execute_governed(self, call: ToolCall, session: SessionRecord,
                                approver) -> ToolResult:
        # 1. repeat-call protection (per-run scope)
        key = (call.name, hashlib.md5(
            json.dumps(call.arguments, sort_keys=True).encode()).hexdigest())
        if self._call_counter[key] >= REPEAT_CALL_LIMIT:
            await self._aemit(session, EventType.PERMISSION_DENIED, {
                "call_id": call.id, "tool": call.name,
                "args": call.arguments, "source": "repeat",
                "reason": f"同一调用重复 {self._call_counter[key]} 次",
            })
            return ToolResult(
                content="已拒绝：该调用重复执行多次，请换一种方式。")
        self._call_counter[key] += 1

        # 2. permission decision
        req = PermissionRequest(
            tool=call.name, args=call.arguments,
            cwd=session.cwd, workspace_root=session.workspace_root)
        decision = self.permissions.decide(req)

        if decision.action == PermissionAction.DENY:
            # Leave a trace: without this the transcript cannot answer "was a
            # dangerous operation refused during this run?".
            await self._aemit(session, EventType.PERMISSION_DENIED, {
                "call_id": call.id, "tool": call.name,
                "args": call.arguments, "source": "hard_deny",
                "reason": decision.reason,
            })
            return ToolResult(content=f"已拒绝：{decision.reason}")

        if decision.action == PermissionAction.ASK:
            # A16: surface the request over SSE, then block on the gate.
            req.args["__call_id"] = call.id
            await self._aemit(session, EventType.PERMISSION_REQUEST, {
                "call_id": call.id,
                "tool": call.name,
                "args": call.arguments,
                "reason": decision.reason,
                "allow_remember": decision.allow_remember,
                **self._overwrite_hint(call, session),
            })
            choice = await approver.wait(req, timeout=300)
            if choice is None:
                # A17: timed out — never retro-execute (B03)
                await self._aemit(session, EventType.PERMISSION_EXPIRED,
                                  {"call_id": call.id})
                return ToolResult(content="已拒绝：权限请求超时")
            if choice.action == PermissionAction.DENY:
                await self._aemit(session, EventType.PERMISSION_RESOLVED,
                                  {"call_id": call.id, "action": "deny"})
                return ToolResult(content="已拒绝：用户未授权该操作")
            await self._aemit(session, EventType.PERMISSION_RESOLVED,
                              {"call_id": call.id, "action": choice.action.value})

        # 3b. D1 — a loaded skill can only NARROW the harness policy.
        ok, reason = self._skill_authorize(call)
        if not ok:
            await self._aemit(session, EventType.PERMISSION_DENIED, {
                "call_id": call.id, "tool": call.name,
                "args": call.arguments, "source": "skill_narrow",
                "reason": reason,
            })
            return ToolResult(content=reason)

        # 3. execute (failures become data)
        # 所有工具调用都走 anyio.to_thread,避免同步工具阻塞 async 事件循环。
        # task 工具已在此线程池里跑,其他如 bash/read_file/glob/grep 等
        # 同步工具也必须走同一线程池,否则整个 SSE 流和健康检查都会被卡住,
        # Electron watchdog 会判定后端崩溃。
        result = await anyio.to_thread.run_sync(
            self.tools.dispatch, call, self._tool_ctx(session))
        if self.memory is not None:
            self.memory.record_tool_stat(session.id, call.name)
        if self.skills is not None and call.name == "use_skill":
            # surface freshly loaded skill content as a dedicated event
            await self._aemit(session, EventType.SKILL_LOADED, {
                "title": call.arguments.get("title", ""),
                "loaded": sorted(self.skills.loaded),
            })
        return result

    def _skill_authorize(self, call: ToolCall) -> tuple[bool, str]:
        """P5/D1: enforce the loaded skills' declarative manifest."""
        if self.skills is None:
            return True, "ok"
        loaded = list(self.skills.loaded.values())
        if not loaded:
            return True, "ok"          # no skill loaded -> harness policy alone
        path = call.arguments.get("path")
        reasons: list[str] = []
        for skill in loaded:
            ok, reason = authorize_skill_tool(call.name, path, skill, True)
            if ok:
                return True, "ok"
            reasons.append(reason)
        return False, reasons[0] if reasons else "已拒绝：技能未声明该操作"

    @staticmethod
    def _check_first_turn_reasoning(text: str) -> bool:
        """Check if the first-turn reasoning is meaningful enough.

        Must satisfy BOTH:
          1. Length >= FIRST_TURN_REASONING_MIN_LEN characters
          2. Contains at least one keyword from each of TWO categories:
             a) Task classification: 简单/复杂/单步/多步/simple/complex/单任务/多任务
             b) Plan or delegate: 步骤/计划/plan/sub-agent/subagent/task/委托/delegate/探索/explore

        Returns True if the reasoning passes, False if it's too shallow.
        """
        t = text.strip()
        if len(t) < FIRST_TURN_REASONING_MIN_LEN:
            return False

        # Category A: task classification
        CLASSIFY_KEYWORDS = [
            "简单", "复杂", "单步", "多步", "单任务", "多任务",
            "simple", "complex", "single", "multi-step", "multistep",
            "one-step", "3+", "several", "many",
        ]
        # Category B: plan or delegate intent
        PLAN_KEYWORDS = [
            "步骤", "计划", "方案", "plan", "steps", "approach",
            "sub-agent", "subagent", "task 工具", "委托", "delegate",
            "探索", "explore", "搜索", "grep", "glob", "find",
            "read", "write", "edit", "read_file", "write_file", "edit_file",
            "工具", "tool", "直接", "执行", "调用",
        ]

        has_classify = any(kw.lower() in t.lower() for kw in CLASSIFY_KEYWORDS)
        has_plan = any(kw.lower() in t.lower() for kw in PLAN_KEYWORDS)

        if not has_classify:
            log.debug("first-turn reasoning missing task classification: %s", t[:80])
        if not has_plan:
            log.debug("first-turn reasoning missing plan/delegate keyword: %s", t[:80])

        return has_classify and has_plan

    def _summarize_tool_calls(self, tool_calls: list) -> str:
        """把 ToolCall 列表转为简洁的自然语言说明。

        例如: [ToolCall(name='write_file', arguments={'path':'x.txt'}),
               ToolCall(name='read_file', arguments={'path':'y.txt'})]
           → "我来创建 x.txt, 读取 y.txt"
        """
        action_map = {
            "write_file": ("创建", lambda a: a.get("path", "")),
            "edit_file": ("修改", lambda a: a.get("path", "")),
            "delete_file": ("删除", lambda a: a.get("path", "")),
            "move_file": ("移动", lambda a: a.get("src", "")),
            "read_file": ("读取", lambda a: a.get("path", "")),
            "glob": ("搜索", lambda a: a.get("pattern", "")),
            "grep": ("搜索", lambda a: a.get("pattern", "")),
            "bash": ("执行命令", lambda a: a.get("command", "")),
            "present_files": ("展示", lambda a: str(a.get("files", ""))),
            "list_changes": ("查看变更历史", lambda a: ""),
            "rollback_file": ("回滚文件", lambda a: a.get("path", "")),
            "rollback_session": ("回滚全部改动", lambda a: ""),
        }
        parts = []
        for tc in tool_calls:
            verb = action_map.get(tc.name, (tc.name, lambda a: ""))[0]
            obj = action_map.get(tc.name, (tc.name, lambda a: ""))[1](tc.arguments)
            if obj:
                # 只取文件名,去掉路径前缀
                short = obj.split("/")[-1].split("\\")[-1][:40]
                parts.append(f"{verb} {short}")
            else:
                parts.append(verb)
        return "我来" + ", ".join(parts)

    # --- usage accounting (A22) ---------------------------------------------
    def _record_usage(self, session: SessionRecord, system: str,
                      messages: list[dict], turn: ModelTurn) -> None:
        if self.memory is None:
            return
        u = turn.usage
        if u:
            prompt_tokens = int(u.get("prompt_tokens") or 0)
            completion_tokens = int(u.get("completion_tokens") or 0)
            estimated = bool(u.get("estimated", False))
        else:
            prompt_tokens = (estimate_tokens(system) + estimate_messages(messages)
                             + self._wire_ref_tokens(messages))
            completion_tokens = estimate_tokens(turn.text)
            estimated = True
        model = self._model_key
        cost_usd = price(model, prompt_tokens, completion_tokens)
        self.memory.record_usage(
            session.id, model, prompt_tokens, completion_tokens,
            estimated, cost_usd)

    def _overwrite_hint(self, call: ToolCall, session: SessionRecord) -> dict:
        """A07: tell the UI whether a write/edit would clobber an existing file."""
        if call.name not in WRITE_TOOLS:
            return {"overwrite": False}
        sp = self.permissions.scope.safe_path(
            call.arguments.get("path", ""), session.cwd)
        if sp is None or not sp.exists():
            return {"overwrite": False}
        try:
            old = sp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return {"overwrite": True, "existing_bytes": sp.stat().st_size}
        new = call.arguments.get("content", "")
        old_head = "\n".join(old.splitlines()[:5])
        new_head = "\n".join(new.splitlines()[:5])
        preview = (f"--- {call.arguments.get('path')} (existing {len(old)} chars)\n"
                   f"{old_head}\n"
                   f"+++ incoming {len(new)} chars\n{new_head}")
        return {"overwrite": True, "existing_bytes": sp.stat().st_size,
                "diff_preview": preview}

    def _tool_ctx(self, session: SessionRecord):
        from .tools import ToolContext
        from .context.externalize import Externalizer
        from pathlib import Path
        sdir = self.storage._session_dir(session.id, session.workspace_root)
        return ToolContext(
            session_id=session.id,
            workspace_root=session.workspace_root,
            cwd=session.cwd,
            scope=self.permissions.scope,
            backup_root=sdir / "backups",
            externalizer=Externalizer(sdir / "tool-results"),
            bash_timeout=60,
            skill_registry=self.skills,
            storage=self.storage,
            subagent_registry=self.subagents,
            subagent_runner=self.subagent_runner_factory,
            memory=self.memory,
            knowledge=self.knowledge,
            kb_ids=self.kb_ids,
            _parent_session=session,
            _parent_provider=self.provider,
            _parent_tools=self.tools,
        )

    def _system_prompt(self, session: SessionRecord) -> tuple[str, dict]:
        """Return (final_system_text, system_parts).

        system_parts: dict of category -> original text, used by
        ContextUsageCalculator for per-category token breakdown.
        """
        parts: dict[str, str] = {}
        if self.context is not None:
            text, meta = self.context.assemble_system_prompt(
                session, self.memory, self.audit)
            parts = meta.get("segments", {})
        else:
            text, _prompt_src, _is_custom = get_system_prompt()
            parts["role"] = text
        if self.skills is not None:
            index = self.skills.index_block() or ""
            loaded = self.skills.loaded_block() or ""
            if index:
                text = f"{text}\n\n{index}"
            if loaded:
                text = f"{text}\n\n{loaded}"
            skill_text = (index + "\n" + loaded).strip()
            if skill_text:
                parts["skills"] = skill_text
        if self.subagents is not None:
            sub_index = self.subagents.index_block() or ""
            if sub_index:
                text = f"{text}\n\n{sub_index}"
                parts["subagents"] = sub_index
        # s18: 专家包 — replace_core 专家已经替换了 role 段,不再追加;
        # 叠加专家追加在 skills/subagents 之后,不覆盖核心身份。
        if self.expert is not None and not self.expert.replace_core:
            from .experts import expert_block
            eblock = expert_block(self.expert, kb_summary=self.kb_summary)
            text = f"{text}\n\n{eblock}"
            parts["expert"] = eblock
            log.info("expert injected (overlay): %s", self.expert.name)
        elif self.expert is not None and self.expert.replace_core:
            log.info("expert injected (replace_core): %s", self.expert.name)
        # P5: MCP connector summary — injects a short block so the model
        # knows *what* external tools are available and when to use them.
        mcp_block = self._mcp_block()
        if mcp_block:
            text = f"{text}\n\n{mcp_block}"
            parts["connectors"] = mcp_block
        else:
            parts["connectors"] = ""
        text = text + f"\nWorkspace root: {session.workspace_root}"
        return text, parts

    @property
    def _model_key(self) -> str:
        """上下文窗口查表键：优先用真实模型名，offline 等无模型时退回 provider 名。

        CONTEXT_WINDOW 按模型名命名（一个平台挂多个模型，窗口各异），所以
        压缩阈值 / 用量估算都必须传模型名而不是 provider 名。
        """
        return getattr(self.provider, "model", "") or self.provider.name

    def _wire_ref_tokens(self, messages: list[dict]) -> int:
        """估算图片 ref 在 wire 阶段展开后新增的 token。

        buffer 里图片是 ``{"type": "image", "path": ...}`` 的轻引用，发送时才被
        provider 换成 base64 图片；视觉模型按**分辨率**计费（≈ w×h/750），所以
        纯文本估算会把它算成 0，压缩阈值与用量统计必须补上。

        非视觉模型（`supports_images=False`）会把图片降级成一句提示，按整图计费
        会误触压缩、甚至让硬上限预检把 run 直接终止，因此那种情况返回 0。
        """
        try:
            if not getattr(self.provider, "supports_images", True):
                return 0
            from .context.tokens import estimate_images_tokens
            return estimate_images_tokens(messages)
        except Exception:
            log.exception("wire ref token estimate failed (non-fatal)")
            return 0

    def _fixed_overhead(self, system: str, tools_specs) -> int:
        """P0-3: 估算 system prompt + 工具定义的固定 token 开销。

        压缩触发若只数 messages,skills/subagents/MCP 块很大时真实请求会
        先于阈值撞上窗口;这部分开销每个 turn 固定,单独传给压缩器。
        不含图片 ref —— 那部分由 `_wire_ref_tokens` 单独算并叠加进来。
        """
        try:
            from .context.usage import ContextUsageCalculator
            calc = ContextUsageCalculator(self._model_key)
            return calc.calc(system, {}, [], tools_specs).total
        except Exception:
            return 0

    # --- MCP connector prompt block -----------------------------------------
    def _mcp_block(self) -> str:
        """Describe connected MCP connectors so the model uses them.

        Scans the registry for tools starting with ``mcp__`` and groups them
        by connector. Returns an empty string if none are bound.
        """
        names = [n for n in self.tools.names() if n.startswith("mcp__")]
        if not names:
            return ""

        # Group by connector: mcp__github__xxx -> github
        by_conn: dict[str, list[str]] = {}
        for n in names:
            parts = n.split("__", 2)  # ['mcp', 'github', 'xxx']
            if len(parts) >= 3:
                by_conn.setdefault(parts[1], []).append(parts[2])

        lines = ["## MCP 外部工具（联网能力）"]
        lines.append("你已连接以下 MCP 服务器，可以调用它们的工具：\n")
        for conn_name, tool_names in by_conn.items():
            # Try to find human-readable descriptions from specs
            spec_map = {s.name: s.description for s in self.tools.specs()}
            lines.append(f"### {conn_name}")
            for tn in tool_names:
                full = f"mcp__{conn_name}__{tn}"
                desc = spec_map.get(full, "")
                lines.append(f"- `{full}` — {desc}" if desc else f"- `{full}`")
            lines.append("")
        lines.append("当用户需要联网操作（查 GitHub、搜索网页、调用外部 API 等）时，优先使用对应的 mcp__ 工具。")
        return "\n".join(lines)
