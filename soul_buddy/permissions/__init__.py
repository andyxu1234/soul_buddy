"""Permissions package (top-level, not under tools/ — ADR-004 / D1)."""
from .policy import (
    PermissionAction,
    PermissionDecision,
    PermissionPolicy,
    PermissionRequest,
    READ_TOOLS,
    WRITE_TOOLS,
    PRESENT_TOOLS,
    ROLLBACK_TOOLS,
    TASK_TOOLS,
    SKILL_TOOLS,
)
from .gate import AutoApproveGate, PermissionGate
from .scope import WorkspaceScope
from .memory import PermissionMemory

__all__ = [
    "PermissionAction", "PermissionDecision", "PermissionPolicy",
    "PermissionRequest", "READ_TOOLS", "WRITE_TOOLS",
    "PRESENT_TOOLS", "ROLLBACK_TOOLS", "TASK_TOOLS", "SKILL_TOOLS",
    "AutoApproveGate", "PermissionGate", "WorkspaceScope", "PermissionMemory",
]
