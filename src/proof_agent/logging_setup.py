"""Logging setup for proof_agent.

Installs a dual-handler root logger (file + stream) and tees stdout/stderr
into the same log file so the many existing `print()` calls are captured
without rewriting them.

Call `configure_logging()` once, as early as possible, from CLI entry points.
The returned `Path` is the file that received all output.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import IO

from .paths import LOGS_DIR, ensure_directory

_CONFIGURED = False
_LOG_PATH: Path | None = None
_STDOUT_BACKUP: IO[str] | None = None
_STDERR_BACKUP: IO[str] | None = None


class _Tee:
    """Write-splitter: sends every write() to both a primary stream and a file handle."""

    def __init__(self, primary: IO[str], mirror: IO[str]) -> None:
        self._primary = primary
        self._mirror = mirror

    def write(self, data: str) -> int:
        try:
            n = self._primary.write(data)
        except Exception:
            n = len(data)
        try:
            self._mirror.write(data)
            self._mirror.flush()
        except Exception:
            pass
        return n

    def flush(self) -> None:
        for stream in (self._primary, self._mirror):
            try:
                stream.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        try:
            return self._primary.isatty()
        except Exception:
            return False

    def fileno(self) -> int:
        return self._primary.fileno()

    def writable(self) -> bool:
        return True

    def __getattr__(self, item: str):
        return getattr(self._primary, item)


def _build_log_path(tag: str) -> Path:
    ensure_directory(LOGS_DIR)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_tag = "".join(c if c.isalnum() or c in "-_." else "_" for c in tag) or "proof_agent"
    return LOGS_DIR / f"{safe_tag}_{timestamp}.log"


def configure_logging(
    tag: str = "proof_agent",
    *,
    level: int = logging.INFO,
    tee_stdout: bool = True,
    log_path: Path | None = None,
) -> Path:
    """Install file + stream logging handlers and optionally tee stdout/stderr.

    Idempotent: a second call returns the existing path without reconfiguring.
    """
    global _CONFIGURED, _LOG_PATH, _STDOUT_BACKUP, _STDERR_BACKUP
    if _CONFIGURED and _LOG_PATH is not None:
        return _LOG_PATH

    resolved = log_path or _build_log_path(tag)
    ensure_directory(resolved.parent)

    file_handler = logging.handlers.RotatingFileHandler(
        str(resolved),
        maxBytes=int(os.getenv("PROOF_AGENT_LOG_MAX_BYTES", str(200 * 1024 * 1024))),
        backupCount=int(os.getenv("PROOF_AGENT_LOG_BACKUP_COUNT", "3")),
        encoding="utf-8",
        delay=False,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname).1s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    stream_handler = logging.StreamHandler(stream=sys.__stdout__)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(
        logging.Formatter(fmt="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    if tee_stdout:
        _STDOUT_BACKUP = sys.stdout
        _STDERR_BACKUP = sys.stderr
        mirror = open(resolved, "a", encoding="utf-8", buffering=1)
        sys.stdout = _Tee(sys.__stdout__, mirror)
        sys.stderr = _Tee(sys.__stderr__, mirror)

    _CONFIGURED = True
    _LOG_PATH = resolved

    logging.getLogger("proof_agent.logging").info(
        "logging initialized → %s (pid=%s)", resolved, os.getpid()
    )
    return resolved


def current_log_path() -> Path | None:
    return _LOG_PATH
