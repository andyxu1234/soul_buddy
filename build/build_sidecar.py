"""Build the FastAPI sidecar into a single Windows exe via PyInstaller.

Run from the project root (the script resolves its own location):

    python build/build_sidecar.py

Produces ``dist/soul_sidecar.exe`` (onefile). In packaged mode the Electron
shell spawns this executable directly; in dev it falls back to
``python -m soul_buddy.api``.

Design notes
------------
* ``--onefile`` so the shell ships one binary (P1.5 spike proved a
  25 MB exe cold-starts in ~3 s and serves /health 200).
* tiktoken is explicitly excluded (A23: never bundle it — we use a heuristic
  tokenizer, and tiktoken drags in heavy deps / native wheels).
* Other heavy / unused libs (torch, pandas, numpy, pytest, ...) are excluded to
  keep the binary lean.
* Provider modules are listed as hidden imports because they are imported
  lazily inside ``select_provider``; PyInstaller may otherwise miss them at
  runtime.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "build" / "sidecar_entry.py"

ARGS = [
    "-m", "PyInstaller",
    "--onefile",
    "--name", "soul_sidecar",
    "--console",
    "--noconfirm",
    "--clean",
    "--paths", str(ROOT),
    # Defensive / size excludes
    "--exclude-module", "tiktoken",
    "--exclude-module", "numpy",
    "--exclude-module", "pandas",
    "--exclude-module", "scipy",
    "--exclude-module", "torch",
    "--exclude-module", "transformers",
    "--exclude-module", "matplotlib",
    "--exclude-module", "pytest",
    "--exclude-module", "PyQt5",
    "--exclude-module", "PySide2",
    "--exclude-module", "tkinter",
    "--exclude-module", "unittest",
    # Lazily-imported modules: force inclusion
    "--hidden-import", "soul_buddy.providers.deepseek",
    "--hidden-import", "soul_buddy.providers.anthropic",
    "--hidden-import", "soul_buddy.providers.openai_chat",
    "--hidden-import", "soul_buddy.providers.offline",
    "--hidden-import", "soul_buddy.memory.db",
    "--hidden-import", "soul_buddy.mcp.connector",
    "--hidden-import", "soul_buddy.skills.registry",
    str(ENTRY),
]


def main() -> int:
    if not ENTRY.exists():
        print(f"[build_sidecar] entry not found: {ENTRY}", file=sys.stderr)
        return 1
    print(f"[build_sidecar] building from {ENTRY}")
    return subprocess.call([sys.executable, *ARGS], cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
