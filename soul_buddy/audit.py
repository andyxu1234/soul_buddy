"""Tamper-evident audit log — hash chain with a head anchor.

P0 covers the core chain (INV-1) + tamper detection (TC-M5-001/002/003).
The three-state anchor model (OK / DEGRADED / TAMPERED, A10/B01/B02) is
extended in P1; `verify_state()` already returns the richer enum so callers
can adopt it without further changes.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import AUDIT_DIR

GENESIS = "GENESIS"


class AuditState(str):
    OK = "ok"
    EMPTY_OK = "empty_ok"
    DEGRADED = "degraded"
    TAMPERED = "tampered"


@dataclass
class Anchor:
    seq: int
    head_hash: str

    def to_json(self) -> str:
        return json.dumps({"seq": self.seq, "head_hash": self.head_hash})

    @classmethod
    def from_json(cls, s: str) -> "Anchor":
        d = json.loads(s)
        return cls(seq=d["seq"], head_hash=d["head_hash"])


class AuditLog:
    def __init__(self, audit_dir: Path | None = None) -> None:
        self.dir = Path(audit_dir) if audit_dir else AUDIT_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "audit.log"
        self.anchor_path = self.dir / "audit.anchor"
        self._lock = threading.RLock()

    # --- hashing ------------------------------------------------------------
    @staticmethod
    def _hash(prev_hash: str, content: str) -> str:
        return hashlib.sha256(f"{prev_hash}|{content}".encode("utf-8")).hexdigest()

    @staticmethod
    def _canonical(data: dict) -> str:
        return json.dumps(data, sort_keys=True, ensure_ascii=False)

    # --- tail recovery (ADR-003) -------------------------------------------
    def recover_interrupted_append(self) -> None:
        if not self.path.exists():
            return
        with self._lock:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            good = []
            for line in lines:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    json.loads(line)
                    good.append(line)
                except json.JSONDecodeError:
                    break
            if len(good) < len(lines):
                self.path.write_text(
                    "\n".join(good) + ("\n" if good else ""), encoding="utf-8")

    # --- append -------------------------------------------------------------
    def append(self, entry_type: str, data: dict, *, seq: int | None = None,
               transcript_event_id: int | None = None) -> dict:
        data = dict(data)
        if transcript_event_id is not None:
            data.setdefault("transcriptEventId", transcript_event_id)
        with self._lock:
            self.recover_interrupted_append()
            prev_hash, last_seq = self._read_tail()
            new_seq = seq if seq is not None else last_seq + 1
            content = self._canonical(data)
            h = self._hash(prev_hash, content)
            record = {
                "seq": new_seq,
                "type": entry_type,
                "ts": time.time(),
                "data": data,
                "prev_hash": prev_hash,
                "hash": h,
            }
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
            self._write_anchor(Anchor(new_seq, h))
        return record

    def _read_tail(self) -> tuple[str, int]:
        if not self.path.exists():
            return GENESIS, 0
        last_hash = GENESIS
        last_seq = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            last_hash = rec["hash"]
            last_seq = rec["seq"]
        return last_hash, last_seq

    def _write_anchor(self, anchor: Anchor) -> None:
        self.anchor_path.write_text(anchor.to_json(), encoding="utf-8")

    def _read_anchor(self) -> Anchor | None:
        if not self.anchor_path.exists():
            return None
        try:
            return Anchor.from_json(self.anchor_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            return None

    # --- verification -------------------------------------------------------
    def verify(self) -> bool:
        return self.verify_state() in (AuditState.OK, AuditState.EMPTY_OK)

    def verify_state(self) -> str:
        if not self.path.exists() or self.path.stat().st_size == 0:
            # B02: empty chain is fine only if there is no session expecting one.
            return AuditState.EMPTY_OK
        prev_hash = GENESIS
        last_seq = 0
        last_hash = GENESIS
        with self._lock:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    return AuditState.TAMPERED
                expected = self._hash(prev_hash, self._canonical(rec["data"]))
                if expected != rec["hash"] or rec["prev_hash"] != prev_hash:
                    return AuditState.TAMPERED
                prev_hash = rec["hash"]
                last_seq = rec["seq"]
                last_hash = rec["hash"]
        anchor = self._read_anchor()
        if anchor is None:
            return AuditState.DEGRADED  # anchor missing -> rebuildable (A10)
        if anchor.seq != last_seq or anchor.head_hash != last_hash:
            return AuditState.TAMPERED
        return AuditState.OK
