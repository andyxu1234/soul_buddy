"""Binds discovered MCP tools into the ToolRegistry (P5).

MCP tools are registered under their namespaced name (`mcp__conn__tool`) so they
flow through the *same* governed dispatch path as every built-in tool:
  policy.decide (rules above) -> permissions gate -> MCP grant allowlist -> call
"""
from __future__ import annotations

from ..models import ToolResult
from .connector import ConnectorManager


def _make_handler(manager: ConnectorManager, tool_name: str):
    def _handler(args: dict, ctx) -> ToolResult:
        result = manager.call_tool(tool_name, args or {})
        if "error" in result:
            return ToolResult(content=f"MCP 调用被拒绝：{result['error']}",
                              is_error=True)
        return ToolResult(content=result.get("content", ""))
    return _handler


class MCPBridge:
    def __init__(self, manager: ConnectorManager) -> None:
        self.manager = manager

    def bind(self, registry) -> int:
        """Register every discovered MCP tool. Returns how many were bound."""
        bound = 0
        for tool in self.manager.discovered_tools():
            name = tool["name"]
            spec = {
                "name": name,
                "description": tool.get("description") or f"MCP tool {name}",
                "parameters": tool.get("input_schema")
                              or {"type": "object", "properties": {}},
            }
            registry.register(spec, _make_handler(self.manager, name))
            bound += 1
        return bound

    def unbind_connector(self, registry, connector_name: str) -> int:
        """Remove all tools belonging to *connector_name* from the registry.

        Uses the ``mcp__<connector>__`` naming prefix so only that connector's
        tools are touched — other connectors remain registered.
        Returns how many were removed.
        """
        prefix = f"mcp__{connector_name}__"
        return registry.unregister_matching(prefix)

    def unbind_all(self, registry) -> int:
        """Remove every MCP tool from the registry. Returns count."""
        return registry.unregister_matching("mcp__")
