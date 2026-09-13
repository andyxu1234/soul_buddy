"""MCP connector layer (P5) — namespace isolation + permission gating.

Lifecycle: disconnected → trusted (user action) → connected (tools discovered).
Tool names are namespaced `mcp__<connector>__<tool>` so connectors can never
collide and every remote call is auditable.

The permission model is deliberately two-layered (s17):
  * trust  = may this connector process run at all
  * grant  = which of its tools the active context may call (explicit allowlist,
             plus `network` must be true)
"""
from __future__ import annotations

from .bridge import MCPBridge
from .connector import (
    ConnectorManager, FakeTransport, MCPConnector, StdioTransport, Transport,
    namespace, split_namespace,
)
from .grant import MCPPermissionGrant, MCPPermissionError, NO_MCP_PERMISSIONS

__all__ = [
    "MCPPermissionGrant", "MCPPermissionError", "NO_MCP_PERMISSIONS",
    "ConnectorManager", "MCPConnector", "Transport", "StdioTransport",
    "FakeTransport", "namespace", "split_namespace", "MCPBridge",
]
