"""present_files — present deliverable files to the user (P5).

A declarative tool. It does NOT create or modify files — the model should
have written them first (with write_file). present_files bundles those paths
into an ArtifactCard list and emits them to the frontend so the chat can
render a "deliverable card" and the right-panel preview can auto-open.

Design notes:
- Runs in agent._execute_governed like any other tool (permission layer applies).
- The handler simply validates paths through scope.safe_path and returns a
  ToolResult whose content is the JSON of ArtifactCards — the agent loop then
  emits ARTIFACT_PRESENTED so both the SSE bus and the transcript get it.
- URLs (http/https) are allowed directly — they open as web previews.
"""
from __future__ import annotations

from ..artifacts import create_artifact_card


def run_present_files(args: dict, ctx) -> ToolResult:
    """Validate paths and return ArtifactCard JSON for downstream events."""
    from .registry import ToolResult
    scope = ctx.scope
    cwd = ctx.cwd

    files = args.get("files", [])
    if not files:
        return ToolResult(content="present_files called with no files", is_error=True)

    out_cards: list[dict] = []
    errors: list[str] = []

    for idx, entry in enumerate(files):
        if isinstance(entry, str):
            raw = entry
            primary = idx == 0
        elif isinstance(entry, dict):
            raw = entry.get("path", "")
            primary = bool(entry.get("primary", idx == 0))
        else:
            errors.append(f"entry {idx}: unknown type {type(entry).__name__}")
            continue

        if raw.startswith(("http://", "https://")):
            card = create_artifact_card(raw, is_primary=primary)
            out_cards.append(card.to_dict())
            continue

        # Local path — validate via scope
        sp = scope.safe_path(raw, cwd)
        if sp is None:
            errors.append(f"path escapes workspace: {raw}")
            continue

        card = create_artifact_card(str(sp), is_primary=primary)
        out_cards.append(card.to_dict())

    import json as _json
    if errors:
        return ToolResult(
            content=_json.dumps({"cards": out_cards, "errors": errors}, ensure_ascii=False),
            is_error=bool(not out_cards),
        )
    return ToolResult(
        content=_json.dumps({"cards": out_cards}, ensure_ascii=False),
    )
