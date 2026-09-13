"""Logging setup — a single rotating file + optional stdout, wired to root
and uvicorn's error/access loggers so every line ends up in sidecar.log.

Safe to call multiple times (idempotent): the file handler is attached only
once per logger, and existing StreamHandlers (from uvicorn's own log_config)
are left untouched — we only ADD a file sink, never take stdout away.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from .config import (
    LOG_BACKUP_DAYS, LOG_DIR, LOG_LEVEL, SIDECAR_LOG,
)

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def _make_file_handler() -> logging.handlers.TimedRotatingFileHandler:
    """Create (but don't attach) the daily-rotating file handler.

    当天日志写入 sidecar.log；午夜滚动后历史日志重命名为 sidecar.log.YYYY-MM-DD。
    本地时间、保留 LOG_BACKUP_DAYS 天。
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.TimedRotatingFileHandler(
        SIDECAR_LOG,
        when="midnight",
        interval=1,
        backupCount=LOG_BACKUP_DAYS,
        encoding="utf-8",
        utc=False,
    )
    fh.setFormatter(logging.Formatter(_FORMAT, _DATE_FMT))
    return fh


def _make_stream_handler() -> logging.StreamHandler:
    """Stdout handler — keeps dev-mode terminal output intact."""
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter(_FORMAT, _DATE_FMT))
    return sh


def _attach_if_missing(logger: logging.Logger, handler: logging.Handler) -> None:
    """Attach handler to logger only if one of the same type isn't already there."""
    htype = type(handler)
    if any(isinstance(h, htype) for h in logger.handlers):
        return
    logger.addHandler(handler)


def setup_logging() -> Path:
    """Configure root + uvicorn.error + uvicorn.access to write to sidecar.log.

    Returns the resolved log file path for diagnostics. Idempotent.
    """
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

    # --- root logger: file + stdout ---
    root = logging.getLogger()
    root.setLevel(level)

    # Remove any pre-existing default handler set by logging.basicConfig so we
    # don't end up with duplicate stdout lines. Uvicorn's own StreamHandler on
    # uvicorn.error is separate and left in place.
    for h in list(root.handlers):
        root.removeHandler(h)

    root.addHandler(_make_stream_handler())
    root.addHandler(_make_file_handler())

    # --- uvicorn.error / uvicorn.access: propagate to root so they hit the file ---
    for name in ("uvicorn.error", "uvicorn.access"):
        ulog = logging.getLogger(name)
        ulog.setLevel(level)
        ulog.propagate = True          # bubble up to root's handlers

    # Quiet noisy third-party loggers that would otherwise spam the file.
    for name in ("httpx", "httpcore", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)

    root.info("sidecar logging initialised → %s", SIDECAR_LOG)
    return SIDECAR_LOG
