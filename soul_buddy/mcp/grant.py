"""MCP permission grant (P5, ported from s17).

Two separate questions, deliberately kept apart:
  * **trust**  — may this connector *process* run at all? (user decision)
  * **grant**  — which of its remote tools may the *active skill* discover/call?

Every granted tool name must be namespaced `mcp__<connector>__<tool>`, and
`network` must be true for any call to pass. This means an MCP tool can never
be reached unless it was explicitly enumerated — there is no wildcard.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class MCPPermissionError(ValueError):
    """Raised when an MCP permission grant is malformed."""


@dataclass(frozen=True)
class MCPPermissionGrant:
    tools: frozenset[str] = field(default_factory=frozenset)
    network: bool = False

    def __post_init__(self):
        if not isinstance(self.network, bool):
            raise MCPPermissionError("MCP permission network must be true or false")
        if isinstance(self.tools, (str, bytes)):
            raise MCPPermissionError("MCP permission tools must be a collection")
        normalized = frozenset(self.tools)
        bad = [t for t in normalized
               if not isinstance(t, str) or not t.startswith("mcp__")]
        if bad:
            raise MCPPermissionError(
                f"MCP permission tools must use namespaced mcp__ names: {bad}")
        object.__setattr__(self, "tools", normalized)

    def allows(self, tool_name: str) -> bool:
        return self.network and tool_name in self.tools


NO_MCP_PERMISSIONS = MCPPermissionGrant()
