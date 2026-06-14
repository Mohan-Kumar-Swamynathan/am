"""Structured logging for GitHub Actions and local runs."""

from __future__ import annotations

import logging
import os
import sys
import uuid
from typing import Optional

_CONFIGURED = False
_CORRELATION_ID: Optional[str] = None


def setup_logging(level: int = logging.INFO, log_file: str = "aalaya_mani.log") -> None:
    global _CONFIGURED, _CORRELATION_ID
    if _CONFIGURED:
        return
    _CORRELATION_ID = os.environ.get("GITHUB_RUN_ID", str(uuid.uuid4())[:8])
    fmt = (
        "%(asctime)s [%(levelname)s] [cid=%(correlation_id)s] "
        "(%(filename)s:%(lineno)d) — %(message)s"
    )

    class _CorrelationFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.correlation_id = _CORRELATION_ID
            return True

    root = logging.getLogger("aalaya_mani")
    root.setLevel(level)
    root.handlers.clear()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter(fmt))
    stream_handler.addFilter(_CorrelationFilter())
    root.addHandler(stream_handler)

    try:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(fmt))
        file_handler.addFilter(_CorrelationFilter())
        root.addHandler(file_handler)
    except OSError:
        pass

    _CONFIGURED = True


def get_logger(name: str = "aalaya_mani") -> logging.Logger:
    if not _CONFIGURED:
        setup_logging()
    return logging.getLogger(name if name.startswith("aalaya_mani") else f"aalaya_mani.{name}")


def get_correlation_id() -> str:
    if not _CORRELATION_ID:
        setup_logging()
    return _CORRELATION_ID or "local"
