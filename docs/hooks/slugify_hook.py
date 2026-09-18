"""MkDocs hook — install a CJK-preserving ``slugify`` for heading anchors.

Why a hook instead of ``!!python/name:``
----------------------------------------
The obvious way to swap the slugifier is

    markdown_extensions:
      - toc:
          slugify: !!python/name:docs._slugify.slugify

but that makes PyYAML import a module named ``docs`` while parsing the config.
That only works when the repository root happens to be on ``sys.path`` — true
for a local ``mkdocs build`` run from the repo root, false on a clean GitHub
Actions runner, which fails with::

    cannot find module 'docs._slugify' (No module named 'docs')

MkDocs ≥ 1.4 loads ``hooks:`` entries by **path** (no import-by-name), so this
works identically locally and in CI.

Where to patch
--------------
``on_config`` receives the parsed config. Note that by then
``config["markdown_extensions"]`` has already been flattened to a list of
extension *names* (``['toc', 'tables', ...]``) — the per-extension settings live
in ``config["mdx_configs"]`` instead, e.g.::

    mdx_configs == {'toc': {'permalink': True, 'separator': '-'}, ...}

So the slugifier is installed by mutating ``mdx_configs['toc']``.

Why a custom slugify at all
---------------------------
Python-Markdown's built-in slugify strips every non-ASCII character, so

    ## 1. 什么是 AI Agent      ->  anchor: 1-ai-agent   (Chinese dropped!)

Every hand-written table-of-contents entry under
``docs/knowledge/ai-agent-interview-guide/`` links to the CJK form
(``#1-什么是-ai-agent``), so all of them silently jumped back to the top of the
page. The implementation below follows the GitHub convention:

  1. NFC-normalise, strip, casefold
  2. delete full-width / CJK punctuation (``（`` ``）`` ``：`` ``、`` ...)
  3. map every other non-word character (whitespace, ``.`` ``/`` ``+``) to ``-``
  4. collapse runs of ``-``; strip leading / trailing ``-``
"""
from __future__ import annotations

import re
import unicodedata

# Full-width / CJK punctuation -> deleted outright (no separator).
_CJK_PUNCT = re.compile(
    r"[\u3000-\u303f\uFF00-\uFF65\uFFE0-\uFFEF"
    r"\u2018\u2019\u201c\u201d\u2026\u2014\u2013\u00b7]"
)
# Everything else that is not a word char or hyphen -> separator.
_SEP = re.compile(r"[^\w\-]+", re.UNICODE)
_DASH_RUN = re.compile(r"-{2,}")


def slugify(value: str, separator: str = "-") -> str:
    """Return a stable, CJK-preserving anchor id (GitHub convention)."""
    # NFC (not NFKC): full-width punctuation must stay full-width so the
    # delete rule above catches it instead of folding to ASCII first.
    text = unicodedata.normalize("NFC", value or "").strip().casefold()
    text = _CJK_PUNCT.sub("", text)
    text = _SEP.sub(separator, text)
    text = _DASH_RUN.sub(separator, text)
    return text.strip(separator)


def on_config(config):
    """Install the slugifier into the ``toc`` extension's config dict.

    ``mdx_configs`` is the mapping MkDocs feeds to each Markdown extension, so
    this is the correct place to override a per-extension setting. Guarded so a
    missing key never breaks the build.
    """
    mdx_configs = config.get("mdx_configs")
    if isinstance(mdx_configs, dict):
        toc_cfg = mdx_configs.get("toc")
        if isinstance(toc_cfg, dict):
            toc_cfg["slugify"] = slugify
    return config
