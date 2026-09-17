"""Extract ``RunSignals`` from one run's event slice.

Pure functions: no I/O, no ``await``, no provider access. Everything the rules
layer needs is derived here, which means the rules can be unit-tested against a
hand-written event list without spinning up an agent.
"""
from __future__ import annotations

from typing import Any

from ..models import Event, EventType
from .model import (
    PATH_TOOLS, WRITE_TOOL_NAMES, RunSignals, TimelineEntry, ToolCallRecord,
)


def _etype(ev: Event) -> str:
    """Event type as a plain string.

    Persisted events come back from JSONL with a bare string, while events
    emitted live still carry the ``EventType`` enum member — accept both.
    """
    et = ev.type
    if isinstance(et, EventType):
        return et.value
    return str(et)


def _target_of(tool: str, args: dict[str, Any]) -> str:
    """Normalized operation target for ordering checks.

    Only filesystem tools have a stable single target. ``bash`` is deliberately
    excluded: its side effects are not expressible as a path, and guessing them
    from the command string would produce false positives.
    """
    if tool in PATH_TOOLS:
        raw = args.get("path")
        if raw:
            return str(raw).replace("\\", "/").lower()
    return ""


def slice_run(events: list[Event], request_id: str) -> list[Event]:
    """Return the events belonging to one run.

    The agent emits the user ``MESSAGE`` *before* ``RUN_STARTED``, so the slice
    has to reach back one event — otherwise the task text would be lost and G2
    could not tell a write-task from a Q&A.
    """
    start: int | None = None
    for i, ev in enumerate(events):
        if (_etype(ev) == EventType.RUN_STARTED.value
                and (ev.data or {}).get("request_id") == request_id):
            start = i          # last match wins: replay-safe
    if start is None:
        return list(events)

    head = start
    if start > 0:
        prev = events[start - 1]
        if (_etype(prev) == EventType.MESSAGE.value
                and (prev.data or {}).get("role") == "user"):
            head = start - 1

    # Close the slice at the end of this run: a terminating event, or the start
    # of the next run. Without this, replaying a transcript would attribute
    # later runs' signals to this one.
    end = len(events)
    for j in range(start + 1, len(events)):
        etype = _etype(events[j])
        if etype == EventType.RUN_STARTED.value:
            end = j
            # The next run's user MESSAGE immediately precedes its RUN_STARTED
            # (same one-back rule as `head`), so it must not leak in here.
            if (_etype(events[j - 1]) == EventType.MESSAGE.value
                    and (events[j - 1].data or {}).get("role") == "user"):
                end = j - 1
            break
        if etype in (EventType.RUN_FINISHED.value,
                     EventType.RUN_ABORTED.value):
            end = j + 1
            break
    return list(events[head:end])


def from_events(events: list[Event], *,
                final_text: str = "",
                turns_used: int = 0,
                max_turns: int = 0,
                modified_files: list[str] | None = None,
                diff_text: str = "") -> RunSignals:
    """Build a ``RunSignals`` snapshot from an already-sliced event list."""
    sig = RunSignals(
        final_text=final_text,
        turns_used=turns_used,
        max_turns=max_turns,
        modified_files=list(modified_files or []),
        diff_text=diff_text,
    )

    pending: dict[str, ToolCallRecord] = {}
    # call_id -> (tool, target) recorded at ASK time, so a later RESOLVED/EXPIRED
    # can be attributed back to a concrete target.
    ask_targets: dict[str, tuple[str, str]] = {}

    for ev in events:
        etype = _etype(ev)
        d: dict[str, Any] = ev.data or {}

        if etype == EventType.MESSAGE.value:
            # First user message = the original task. Retry feedback may inject
            # further user messages; they are not the task.
            if d.get("role") == "user" and not sig.user_text:
                sig.user_text = str(d.get("text") or "")

        elif etype == EventType.FUNCTION_CALL.value:
            rec = ToolCallRecord(
                call_id=str(d.get("call_id") or ""),
                tool=str(d.get("tool") or ""),
                arguments=d.get("arguments") or {},
            )
            sig.calls.append(rec)
            pending[rec.call_id] = rec
            sig.timeline.append(TimelineEntry(
                kind="call", tool=rec.tool, call_id=rec.call_id,
                target=_target_of(rec.tool, rec.arguments)))

        elif etype == EventType.FUNCTION_CALL_RESULT.value:
            cid = str(d.get("call_id") or "")
            is_err = bool(d.get("is_error"))
            rec = pending.get(cid)
            if rec is not None:
                rec.is_error = is_err
                rec.content = str(d.get("content") or "")
            for entry in reversed(sig.timeline):
                if entry.call_id == cid:
                    entry.is_error = is_err
                    break

        elif etype == EventType.PERMISSION_REQUEST.value:
            sig.asks += 1
            cid = str(d.get("call_id") or "")
            tool = str(d.get("tool") or "")
            target = _target_of(tool, d.get("args") or {})
            ask_targets[cid] = (tool, target)

        elif etype == EventType.PERMISSION_RESOLVED.value:
            if d.get("action") == "deny":
                sig.user_denials += 1
                cid = str(d.get("call_id") or "")
                tool, target = ask_targets.get(cid, ("", ""))
                sig.timeline.append(TimelineEntry(
                    kind="deny", tool=tool, call_id=cid,
                    target=target, source="user"))

        elif etype == EventType.PERMISSION_EXPIRED.value:
            sig.expirations += 1
            sig.timeline.append(TimelineEntry(
                kind="expired", call_id=str(d.get("call_id") or "")))

        elif etype == EventType.PERMISSION_DENIED.value:
            sig.denials.append(d)
            tool = str(d.get("tool") or "")
            sig.timeline.append(TimelineEntry(
                kind="deny", tool=tool,
                call_id=str(d.get("call_id") or ""),
                target=_target_of(tool, d.get("args") or d.get("arguments") or {}),
                source=str(d.get("source") or "")))

        elif etype == EventType.ARTIFACT_PRESENTED.value:
            sig.artifacts += 1

        elif etype in (EventType.REASONING.value,
                       EventType.REASONING_DELTA.value):
            if d.get("text"):
                sig.has_reasoning = True

        elif etype == EventType.TURN_BUDGET_WARNING.value:
            sig.budget_warning = True

    # ``modified_files`` is derivable from the event stream itself, so only
    # fall back to it when the caller did not supply its own list. Deriving it
    # here keeps the rules layer honest: a file only counts as modified if a
    # write actually succeeded.
    if modified_files is None:
        sig.modified_files = _derive_modified_files(sig.calls)
    return sig


def _derive_modified_files(calls: list[ToolCallRecord]) -> list[str]:
    """Paths touched by successful write/edit calls, de-duplicated in order."""
    out: list[str] = []
    seen: set[str] = set()
    for rec in calls:
        if rec.tool not in WRITE_TOOL_NAMES or rec.is_error is not False:
            continue
        path = rec.arguments.get("path")
        if path and path not in seen:
            seen.add(path)
            out.append(str(path))
    return out
