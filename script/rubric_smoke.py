"""End-to-end smoke check for the runtime rubric (P6 / M16).

Unlike ``tests/test_rubric.py`` (which unit-tests the layers and asserts on
individual outcomes), this drives a real ``SoulAgent.run()`` through five
scenarios and prints one line per scenario, so a human can see the whole loop —
scoring, gating, retry, judge, persistence — actually working end to end.

Everything runs against scripted offline providers: zero tokens, deterministic.

Usage:
    python script/rubric_smoke.py            # print the scenario table
    python script/rubric_smoke.py --verbose  # also print the aggregated report

Exit code is 0 only when every scenario matches its expectation, so it can be
used as a pre-release gate next to the unit suite.

See docs/modules/20-rubric.md §18 for the recorded results.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "script"))

# The agent's config module reads SOUL_BUDDY_HOME at import time, so the
# sandbox home has to be in place before anything under soul_buddy is imported.
_HOME = Path(tempfile.mkdtemp(prefix="sb_rubric_smoke_"))
os.environ["SOUL_BUDDY_HOME"] = str(_HOME)

from soul_buddy.agent import SoulAgent  # noqa: E402
from soul_buddy.audit import AuditLog  # noqa: E402
from soul_buddy.models import SessionRecord  # noqa: E402
from soul_buddy.permissions import (  # noqa: E402
    AutoApproveGate, PermissionPolicy, WorkspaceScope,
)
from soul_buddy.providers.base import ModelTurn, ToolCall  # noqa: E402
from soul_buddy.providers.offline import OfflineProvider  # noqa: E402
from soul_buddy.rubric import RubricPolicy  # noqa: E402
from soul_buddy.storage import SessionStore  # noqa: E402
from soul_buddy.tools import build_default_registry  # noqa: E402

import rubric_report  # noqa: E402

# A first turn must clear the first-turn reasoning guard: >= 20 chars AND a
# keyword from each of the two categories (task classification + plan/delegate).
# Get this wrong and turn 1 is dropped, the script shifts by one, and the run
# ends with "no tools were ever called" — which looks like a signals bug.
OPEN_OK = "这是一个简单单步任务，我打算直接调用工具完成，步骤只有一步。"
OPEN_FAIL = "这是一个简单单步任务，我打算先看一下文件再决定，步骤可能有两步。"

TERMINAL_OK = "已写入目标文件，本次只有一个文件被改动，没有遗留风险。"


class _NoopBus:
    async def publish(self, *a, **k):
        return None


class _ScriptedJudgeProvider(OfflineProvider):
    """Scripted for the main loop, and answers the judge prompts with JSON."""

    # Capability flag, not a name check: OfflineProvider sets this to False so
    # the judge degrades, and a test double opts back in here.
    llm_backed = True

    def create(self, req):
        if req.tools == [] and "评审员" in (getattr(req, "system", "") or ""):
            return ModelTurn(text=(
                '{"scores":[{"id":"Q5","score":2,"reason":"改动正确但缺少注释"},'
                '{"id":"Q6","score":3,"reason":"答复说清了改动内容"}]}'))
        return super().create(req)


def _call(cid: str, tool: str, args: dict) -> ToolCall:
    return ToolCall(id=cid, name=tool, arguments=args)


async def _run(ws: Path, task: str, script: list, *, mode: str,
               provider_cls=OfflineProvider):
    storage = SessionStore()
    session = SessionRecord.create(str(ws))
    storage.save_session(session)
    provider = provider_cls()
    provider.set_script(script)
    agent = SoulAgent(storage, build_default_registry(), _NoopBus(), AuditLog(),
                      provider, PermissionPolicy(WorkspaceScope(ws)),
                      rubric=RubricPolicy(mode=mode))
    return await agent.run(session, task, AutoApproveGate())


def _scores(res) -> dict[str, int | None]:
    r = res.rubric or {}
    return {d["id"]: d["score"]
            for group in ("gating", "quality") for d in r.get(group, [])}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true",
                        help="also print the aggregated §7.3 report")
    parser.add_argument("--out", help="also write the output to this path")
    args = parser.parse_args()

    buffer: list[str] = []
    _emit = print

    def emit(line: str = "") -> None:
        buffer.append(line)
        _emit(line)

    ws = _HOME / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "a.txt").write_text("hello", encoding="utf-8")

    results: list[tuple[str, object, callable]] = []

    # 1. advisory, the task actually got done -> every gate clears.
    results.append(("1 advisory / task done", await _run(ws, "写一个 out.txt", [
        ModelTurn(text=OPEN_OK, tool_calls=[
            _call("c1", "write_file", {"path": "out.txt", "content": "hi"})]),
        ModelTurn(text=TERMINAL_OK),
    ], mode="advisory"), lambda r: r.rubric["passed"] and r.rubric["total"] == 80))

    # 2. advisory, the user asked for work but nothing was written -> G2 fails.
    results.append(("2 advisory / no change", await _run(ws, "帮我修改 a.txt 的内容", [
        ModelTurn(text=OPEN_FAIL, tool_calls=[
            _call("c2", "read_file", {"path": "a.txt"})]),
        ModelTurn(text="我看了一下 a.txt，内容如你所见，无需改动。"),
    ], mode="advisory"),
        lambda r: not r.rubric["passed"] and r.rubric["failed_gating"] == ["G2"]))

    # 3. enforce, fails once then succeeds -> exactly one retry.
    results.append(("3 enforce / retry then pass", await _run(ws, "创建 b.txt", [
        ModelTurn(text=OPEN_FAIL, tool_calls=[
            _call("c3", "read_file", {"path": "a.txt"})]),
        ModelTurn(text="我先看了一下目录，暂时没有落盘。"),
        ModelTurn(text=OPEN_OK, tool_calls=[
            _call("c4", "write_file", {"path": "b.txt", "content": "ok"})]),
        ModelTurn(text=TERMINAL_OK),
    ], mode="enforce"),
        lambda r: (r.rubric["passed"] and r.rubric["retries_used"] == 1
                   and (ws / "b.txt").exists())))

    # 4. advisory with a judge that answers -> the LLM path is not degraded.
    results.append(("4 advisory / judge live", await _run(ws, "写一个 c.txt", [
        ModelTurn(text=OPEN_OK, tool_calls=[
            _call("c5", "write_file", {"path": "c.txt", "content": "hi"})]),
        ModelTurn(text=TERMINAL_OK),
    ], mode="advisory", provider_cls=_ScriptedJudgeProvider),
        lambda r: r.rubric["degraded"] is False and _scores(r)["Q5"] == 2))

    # 5. off mode must be indistinguishable from the pre-rubric agent (INV-21).
    results.append(("5 off / untouched (INV-21)", await _run(ws, "写一个 d.txt", [
        ModelTurn(text=OPEN_OK, tool_calls=[
            _call("c6", "write_file", {"path": "d.txt", "content": "hi"})]),
        ModelTurn(text=TERMINAL_OK),
    ], mode="off"), lambda r: r.rubric is None))

    ok = True
    width = max(len(name) for name, _, _ in results)
    emit(f"home: {_HOME}\n")
    for name, res, check in results:
        s = _scores(res)
        row = (f"G1={s.get('G1')} G2={s.get('G2')} G3={s.get('G3')} "
               f"Q1={s.get('Q1')} Q2={s.get('Q2')} Q3={s.get('Q3')} "
               f"Q4={s.get('Q4')} Q5={s.get('Q5')} Q6={s.get('Q6')}")
        verdict = "OK  " if check(res) else "FAIL"
        ok = ok and verdict == "OK  "
        if res.rubric is None:
            emit(f"[{verdict}] {name:<{width}}  rubric=None  {row}")
        else:
            emit(f"[{verdict}] {name:<{width}}  "
                 f"passed={str(res.rubric['passed']):<5} "
                 f"total={res.rubric['total']:<4} "
                 f"degraded={str(res.rubric['degraded']):<5} "
                 f"retries={res.rubric['retries_used']}  {row}")

    reports = rubric_report.find_reports(_HOME)
    emit(f"\nreports on disk: {len(reports)}  (expect 4 — off writes none)")
    if reports:
        summary = rubric_report.summarise(reports)
        if args.verbose:
            emit("\n" + rubric_report.render(summary))
        else:
            emit(f"pass rate: {summary['pass_rate']:.0%}  "
                 f"modes={summary['modes']}  degraded={summary['degraded']}")

    emit("\n" + ("all scenarios OK" if ok else "SOME SCENARIOS FAILED"))
    if args.out:
        Path(args.out).write_text("\n".join(buffer) + "\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
