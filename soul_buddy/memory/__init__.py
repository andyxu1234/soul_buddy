"""Memory package (P3): three-tier recall + SQLite derived index.

Layers (B07 priority: user > workspace > cloud):
  user       — explicit user preferences (highest authority)
  workspace  — project-scoped facts inferred per workspace
  cloud      — remote/synced inference (stub in P3)

The SQLite index (db.MemoryDB) is fully derived from the JSONL transcript and is
rebuildable; the `memory` table itself is a source of truth and is not rebuilt.
"""
from __future__ import annotations

from .cloud import CloudMemory
from .db import MemoryDB, MemoryItem
from .manager import MemoryManager
from .pricing import PRICING, price
from .projections import USER_MD_NAME, USER_MEMORY_MD_NAME, memory_dir
from .user import UserMemory
from .workspace import WorkspaceMemory

# --- 长期记忆 v3(轨 B/C) --------------------------------------------------
from .capture import capture_new, new_raw_events
from .extract import CandidateExtractor, make_extract_provider
from .longterm import (
    Atom, Candidate, LongTermMemory, RawEvent, new_id,
)
from .promote import promote_candidates
from .recall import recall_for_prompt
from .schedule import distill_all, distill_session
from .wiring import LongTermMemoryWiring

__all__ = [
    "MemoryDB", "MemoryItem", "MemoryManager",
    "UserMemory", "WorkspaceMemory", "CloudMemory",
    "PRICING", "price",
    "USER_MD_NAME", "USER_MEMORY_MD_NAME", "memory_dir",
    # 长期记忆 v3
    "LongTermMemory", "RawEvent", "Candidate", "Atom", "new_id",
    "capture_new", "new_raw_events", "recall_for_prompt",
    "CandidateExtractor", "make_extract_provider", "promote_candidates",
    "distill_session", "distill_all", "LongTermMemoryWiring",
]
