"""Heuristic token estimator (A23 / ADR-009).

Default strategy: no tiktoken dependency (it downloads a BPE vocab at runtime,
which breaks in the PyInstaller package). Chinese chars weigh ~1.5 tokens,
ASCII words ~len/4. The 25% compaction margin (A13) makes this precise enough.
tiktoken remains an optional enhancement enabled only after the P1.5 Spike proves
it loads in the packaged build.
"""
from __future__ import annotations


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    total = 0
    for ch in text:
        cp = ord(ch)
        if cp >= 0x4E00 and cp <= 0x9FFF:       # CJK ideograph ~1.5
            total += 1.5
        elif ch.isspace():
            total += 0.25
        elif ord(ch) < 128:
            total += 0.25
        else:
            total += 0.5
    return int(total) + 1


def estimate_messages(messages: list[dict]) -> int:
    """Estimate tokens across an Anthropic/OpenAI-shaped message buffer."""
    try:
        import json

        return estimate_tokens(json.dumps(messages, ensure_ascii=False))
    except Exception:
        return estimate_tokens(
            " ".join(str(m.get("content", "")) for m in messages))
