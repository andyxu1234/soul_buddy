"""PyInstaller entry shim for the sidecar.

PyInstaller runs this as a top-level script, so it must import the
``soul_buddy.api`` package *as a package* (giving ``__main__.py`` a parent
package) — direct use of ``soul_buddy/api/__main__.py`` would break its
relative import (``from .main import create_app``). This keeps the
``python -m soul_buddy.api`` entry point untouched.
"""
from soul_buddy.api.__main__ import main

if __name__ == "__main__":
    main()
