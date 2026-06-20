"""Logging setup for the scheduler daemon (first real handler setup in dsync)."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_LOG_NAME = "dsync"


def get_scheduler_logger(config_dir: Path) -> logging.Logger:
    """Configure dsync file/stream logging and return the scheduler logger.

    Handlers are attached to the parent ``dsync`` logger so sync internals (e.g.
    file transfer) are captured alongside the scheduler's own lines. Idempotent:
    calling it again does not add duplicate handlers.

    Args:
        config_dir: Directory holding the dsync configuration; the log file is
            written to ``config_dir/logs/dsync-scheduler.log``.

    Returns:
        The ``dsync.scheduler`` logger for emitting run records.
    """
    parent = logging.getLogger(_LOG_NAME)
    if not parent.handlers:
        parent.setLevel(logging.INFO)
        log_path = config_dir / "logs" / "dsync-scheduler.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        parent.addHandler(file_handler)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        parent.addHandler(stream_handler)

    return logging.getLogger(f"{_LOG_NAME}.scheduler")
