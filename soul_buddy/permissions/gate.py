"""Permission gate — serializes ask() decisions and resolves them.

P0 ships `AutoApproveGate` (dev/test mode, no UI yet). The full async
`PermissionGate` with SSE-driven resolution + single FIFO queue + `deny_rest`
is wired in P1/P4; it is implemented here already so the API can adopt it.

Contract (A16/B03): only one ask is pending at a time (tool calls are executed
serially, so the non-head branch is a defensive fuse, not a normal path).
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from .policy import PermissionAction, PermissionDecision, PermissionRequest

TIMEOUT = 300.0  # BR-13


class AutoApproveGate:
    """Dev/test gate: resolves every ASK as ALLOW immediately."""

    async def wait(self, req: PermissionRequest, timeout: float = TIMEOUT):
        return PermissionDecision(PermissionAction.ALLOW, "auto_approve",
                                  "auto-approve dev mode")


class PermissionGate:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Event] = {}
        self._results: dict[str, PermissionDecision] = {}
        self._queue: list[str] = []
        self.deny_rest = False
        self.allow_rest = False

    async def wait(self, req: PermissionRequest, timeout: float = TIMEOUT) \
            -> PermissionDecision:
        if self.deny_rest:
            return PermissionDecision(PermissionAction.DENY, "deny_rest",
                                     "deny_rest active for this run",
                                     allow_remember=False)
        if self.allow_rest:
            return PermissionDecision(PermissionAction.ALLOW, "allow_rest",
                                     "allow_rest active for this run",
                                     allow_remember=False)
        call_id = req.args.get("__call_id") or id(req)
        async with self._lock:
            if self._queue and self._queue[0] != call_id and call_id in self._queue:
                return PermissionDecision(PermissionAction.DENY,
                                         "permission_out_of_order",
                                         "ask arrived out of FIFO order",
                                         allow_remember=False)
            self._queue.append(call_id)
            ev = asyncio.Event()
            self._pending[call_id] = ev
        try:
            try:
                await asyncio.wait_for(ev.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return PermissionDecision(PermissionAction.DENY, "permission_timeout",
                                         "ask timed out", allow_remember=False)
            return self._results.get(call_id, PermissionDecision(
                PermissionAction.DENY, "permission_timeout", "no result",
                allow_remember=False))
        finally:
            async with self._lock:
                self._queue = [c for c in self._queue if c != call_id]
                self._pending.pop(call_id, None)
                self._results.pop(call_id, None)

    def abort_pending(self) -> int:
        """Release every pending ask as DENY (used when a run is aborted).

        Returns the number of released waits. Without this, a run blocked on a
        permission prompt would only unwind after the 300s gate timeout.
        """
        n = 0
        for call_id, ev in list(self._pending.items()):
            self._results[call_id] = PermissionDecision(
                PermissionAction.DENY, "run_aborted",
                "run aborted while awaiting authorization",
                allow_remember=False)
            ev.set()
            n += 1
        return n

    def reset_run_flags(self) -> None:
        """Reset per-run shortcuts (deny_rest / allow_rest). Called at run start."""
        self.deny_rest = False
        self.allow_rest = False

    async def resolve(self, call_id: str, choice: str) -> bool:
        async with self._lock:
            if call_id not in self._pending:
                return False
            action = {
                "allow_once": PermissionAction.ALLOW,
                "allow_dir": PermissionAction.ALLOW,
                "allow_rest": PermissionAction.ALLOW,
                "deny": PermissionAction.DENY,
                "deny_rest": PermissionAction.DENY,
            }.get(choice, PermissionAction.DENY)
            dec = PermissionDecision(
                action,
                "user_resolved",
                f"user chose {choice}",
                allow_remember=(choice == "allow_dir"),
            )
            if choice == "deny_rest":
                self.deny_rest = True
            if choice == "allow_rest":
                self.allow_rest = True
            self._results[call_id] = dec
            self._pending[call_id].set()
            return True
