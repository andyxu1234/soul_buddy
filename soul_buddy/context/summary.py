"""Provider-backed summary callable for the compaction L4 layer (A12 / P0-1).

The runtime wires `make_summary_provider(provider)` into the ContextLayer so
the summary layer actually runs in production. The call is synchronous and
must stay cheap: small max_tokens, no tools. Any exception propagates — the
CompactController owns the degradation path (summary_failed -> prune).
"""
from __future__ import annotations

from typing import Callable

from ..providers.base import ModelTurn, Provider, ProviderRequest

_SUMMARY_SYSTEM = (
    "You are a compaction assistant inside a coding agent. Summarize "
    "faithfully; never invent facts. Follow the requested output format "
    "exactly."
)


def make_summary_provider(provider: Provider) -> Callable[[str], str]:
    """Build a sync `Callable[[str], str]` summarizer over a Provider.

    Shared by the main agent's CompactController and sub-agent runners so
    both loops get a working L4 layer from the same provider wiring.
    """
    def summarize(prompt: str) -> str:
        req = ProviderRequest(
            system=_SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            max_tokens=1024,
        )
        turn: ModelTurn = provider.create(req)
        return turn.text or ""

    return summarize
