"""MCP connectors: lifecycle, namespace isolation, permission gating (P5, s17).

A connector moves through disconnected → trusted → connected. Trust is an
explicit user action; nothing is spawned until it is granted.

Namespacing: a remote tool `search` on connector `brave` is exposed to the model
as `mcp__brave__search`. Collision between connectors is therefore impossible,
and the prefix makes every MCP call trivially auditable.

Transport is injectable so the whole layer is testable without spawning a real
MCP server (see `FakeTransport`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from .grant import MCPPermissionGrant, NO_MCP_PERMISSIONS


# ---------------------------------------------------------------------------
# transports
# ---------------------------------------------------------------------------
class Transport:
    """Minimal JSON-RPC request/response transport."""

    def start(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def request(self, method: str, params: dict | None = None) -> dict:
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class StdioTransport(Transport):
    """JSON-RPC over a subprocess' stdin/stdout (the real MCP stdio mode)."""

    def __init__(self, command: str, args: Iterable[str] | None = None,
                 env: dict | None = None) -> None:
        self.command = command
        self.args = list(args or [])
        self.env = env
        self._proc = None
        self._id = 0

    def start(self) -> None:
        import shutil
        import subprocess

        # Resolve command via PATH so Windows .cmd/.bat shims (npx.cmd,
        # npm.cmd) are found — subprocess.Popen with shell=False does NOT
        # look up .cmd files itself, which is why bare "npx" fails with
        # [WinError 2] on Windows.
        cmd_path = shutil.which(self.command) or self.command

        self._proc = subprocess.Popen(
            [cmd_path, *self.args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, env=self.env,
        )

    def request(self, method: str, params: dict | None = None) -> dict:
        if self._proc is None:
            return {"error": "transport not started"}
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method,
                   "params": params or {}}
        try:
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
            line = self._proc.stdout.readline()
            if not line:
                return {"error": "no response from connector"}
            return json.loads(line)
        except Exception as exc:
            return {"error": str(exc)}

    def close(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None


class FakeTransport(Transport):
    """In-memory transport for tests — no subprocess, deterministic responses."""

    def __init__(self, tools: list[dict] | None = None,
                 results: dict[str, Any] | None = None) -> None:
        self.tools = tools or []
        self.results = results or {}
        self.started = False
        self.calls: list[tuple[str, dict]] = []

    def start(self) -> None:
        self.started = True

    def request(self, method: str, params: dict | None = None) -> dict:
        params = params or {}
        if method == "tools/list":
            return {"result": {"tools": self.tools}}
        if method == "tools/call":
            name = params.get("name", "")
            self.calls.append((name, params.get("arguments") or {}))
            if name in self.results:
                return {"result": {"content": self.results[name]}}
            return {"result": {"content": f"ok:{name}"}}
        if method == "initialize":
            return {"result": {"protocolVersion": "1.0"}}
        return {"result": {}}

    def close(self) -> None:
        self.started = False


# ---------------------------------------------------------------------------
# connector
# ---------------------------------------------------------------------------
def namespace(connector: str, tool: str) -> str:
    return f"mcp__{connector}__{tool}"


def split_namespace(name: str) -> tuple[str, str] | None:
    """`mcp__brave__search` -> ('brave', 'search'); None if malformed."""
    parts = name.split("__")
    if len(parts) < 3 or parts[0] != "mcp":
        return None
    return parts[1], "__".join(parts[2:])


@dataclass
class MCPConnector:
    name: str
    config: dict
    transport: Transport | None = None
    status: str = "disconnected"          # disconnected|trusted|connected
    tools: list[dict] = field(default_factory=list)
    trusted: bool = False

    def trust(self) -> None:
        self.trusted = True
        self.status = "trusted"

    def connect(self) -> bool:
        if not self.trusted:
            return False
        if self.transport is None:
            # Merge config env vars on top of os.environ so PATH / etc. are
            # preserved. Passing env=None would also work (inherit everything)
            # but we want to let users override specific vars.
            env = self.config.get("env")
            if env:
                import os as _os
                merged = dict(_os.environ)
                merged.update(env)
                env = merged
            self.transport = StdioTransport(
                self.config.get("command", ""),
                self.config.get("args", []),
                env,
            )
        self.status = "connecting"
        try:
            self.transport.start()
            self.transport.request("initialize")
            resp = self.transport.request("tools/list")
            raw = (resp.get("result") or {}).get("tools", [])
            self.tools = [
                {
                    "name": namespace(self.name, t["name"]),
                    "description": t.get("description", ""),
                    "input_schema": t.get("inputSchema") or t.get("input_schema")
                                    or {"type": "object", "properties": {}},
                    "_connector": self.name,
                    "_original_name": t["name"],
                }
                for t in raw if isinstance(t, dict) and t.get("name")
            ]
            self.status = "connected"
            return True
        except Exception as exc:
            self.status = f"error: {exc}"
            return False

    def disconnect(self) -> None:
        if self.transport is not None:
            try:
                self.transport.close()
            except Exception:
                pass
        self.status = "disconnected"
        self.tools = []

    def call_tool(self, namespaced: str, params: dict) -> str:
        if self.transport is None:
            return "Error: connector not connected"
        original = next((t["_original_name"] for t in self.tools
                         if t["name"] == namespaced),
                        (split_namespace(namespaced) or ("", namespaced))[1])
        resp = self.transport.request(
            "tools/call", {"name": original, "arguments": params})
        if "error" in resp:
            return f"Error: {resp['error']}"
        result = resp.get("result") or {}
        content = result.get("content")
        if isinstance(content, list):
            return "\n".join(
                c.get("text", "") for c in content if isinstance(c, dict))
        return str(content) if content is not None else json.dumps(
            result, ensure_ascii=False)


class ConnectorManager:
    """Parses mcp.json, tracks trust, connects, and gates every tool call."""

    def __init__(self, config: dict | None = None,
                 grant: MCPPermissionGrant | None = None) -> None:
        self.connectors: dict[str, MCPConnector] = {}
        self.grant = grant or NO_MCP_PERMISSIONS
        for name, cfg in (config or {}).get("mcpServers", {}).items():
            self.connectors[name] = MCPConnector(name=name, config=cfg or {})

    # --- permissions -------------------------------------------------------
    def set_permission_grant(self, grant: MCPPermissionGrant) -> None:
        self.grant = grant

    def _denial(self, tool_name: str) -> dict:
        reason = ("network access is not declared"
                  if not self.grant.network
                  else "tool is not declared by the active skill")
        return {"error": f"Permission denied for '{tool_name}': {reason}.",
                "code": "permission_denied", "tool": tool_name}

    def allows(self, tool_name: str) -> bool:
        return self.grant.allows(tool_name)

    # --- lifecycle ---------------------------------------------------------
    def trust(self, name: str) -> bool:
        conn = self.connectors.get(name)
        if conn is None:
            return False
        conn.trust()
        return True

    def connect(self, name: str) -> bool:
        conn = self.connectors.get(name)
        if conn is None:
            return False
        return conn.connect()

    def disconnect(self, name: str) -> None:
        conn = self.connectors.get(name)
        if conn:
            conn.disconnect()

    def list_connectors(self) -> list[dict]:
        return [{"name": c.name, "status": c.status, "trusted": c.trusted,
                 "tools": [t["name"] for t in c.tools]}
                for c in self.connectors.values()]

    def discovered_tools(self) -> list[dict]:
        """All namespaced tools from connected connectors."""
        out: list[dict] = []
        for c in self.connectors.values():
            if c.status == "connected":
                out.extend(c.tools)
        return out

    def refresh_grant(self) -> MCPPermissionGrant:
        """Rebuild the allowlist from currently connected connectors.

        Trusting a connector is the user's explicit decision to let it run; the
        grant then enumerates its tools one by one (never a wildcard).
        """
        tools = [t["name"] for t in self.discovered_tools()]
        grant = MCPPermissionGrant(tools=frozenset(tools), network=bool(tools))
        self.set_permission_grant(grant)
        return grant

    def call_tool(self, tool_name: str, params: dict) -> dict:
        """Namespaced call, gated by the active permission grant."""
        if not split_namespace(tool_name):
            return {"error": f"malformed MCP tool name: {tool_name}",
                    "code": "bad_name"}
        if not self.allows(tool_name):
            return self._denial(tool_name)
        parts = split_namespace(tool_name)
        connector = self.connectors.get(parts[0]) if parts else None
        if connector is None:
            return {"error": f"unknown connector for {tool_name}",
                    "code": "unknown_connector"}
        if connector.status != "connected":
            return {"error": f"connector '{parts[0]}' is not connected",
                    "code": "not_connected"}
        return {"content": connector.call_tool(tool_name, params or {})}
