"""Permission memory — directory-level, write/edit only, 30-day TTL (A26 / BR-25).

Lives in `~/.soul_buddy/permissions.json` (NEVER inside a user workspace, so the
agent can't read or mutate its own grants). Bash grants are never remembered
(variants are unbounded). Hard-deny and path-escape denials are never stored.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from ..config import PERMISSIONS_PATH, PERMISSION_TTL_DAYS


class PermissionMemory:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else PERMISSIONS_PATH
        self._rules: list[dict] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._rules = [r for r in data.get("rules", [])
                               if not self._expired(r)]
            except (json.JSONDecodeError, KeyError):
                self._rules = []
        else:
            self._rules = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"version": 1, "rules": self._rules},
                                        ensure_ascii=False, indent=2),
                             encoding="utf-8")

    @staticmethod
    def _expired(rule: dict) -> bool:
        exp = rule.get("expires_at")
        return exp is not None and time.time() > exp

    def remember(self, scope: str, action: str = "allow_dir") -> str:
        rule_id = uuid.uuid4().hex[:12]
        now = time.time()
        self._rules.append({
            "id": rule_id,
            "scope": str(Path(scope).resolve()),
            "action": action,
            "created_at": now,
            "expires_at": now + PERMISSION_TTL_DAYS * 86400,
        })
        self._save()
        return rule_id

    def match(self, scope: str) -> str | None:
        """Return a matching (non-expired) rule id for this directory, else None."""
        target = Path(scope).resolve()
        for r in self._rules:
            if self._expired(r):
                continue
            try:
                if target.is_relative_to(Path(r["scope"])):
                    return r["id"]
            except ValueError:
                continue
        return None

    def revoke(self, rule_id: str) -> bool:
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.get("id") != rule_id]
        if len(self._rules) != before:
            self._save()
            return True
        return False

    def list_rules(self) -> list[dict]:
        return [r for r in self._rules if not self._expired(r)]
