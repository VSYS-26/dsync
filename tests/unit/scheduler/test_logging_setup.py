import logging
from pathlib import Path

import pytest

from dsync.scheduler.logging_setup import get_scheduler_logger


@pytest.fixture(autouse=True)
def _reset_dsync_logger_handlers():
    parent = logging.getLogger("dsync")
    original = list(parent.handlers)
    parent.handlers = []
    yield
    for handler in parent.handlers:
        handler.close()
    parent.handlers = original


def test_returns_scheduler_named_logger(tmp_path: Path) -> None:
    logger = get_scheduler_logger(tmp_path)
    assert logger.name == "dsync.scheduler"


def test_creates_log_file(tmp_path: Path) -> None:
    get_scheduler_logger(tmp_path)
    assert (tmp_path / "logs" / "dsync-scheduler.log").is_file()


def test_idempotent_does_not_duplicate_handlers(tmp_path: Path) -> None:
    parent = logging.getLogger("dsync")

    get_scheduler_logger(tmp_path)
    handler_count_after_first = len(parent.handlers)
    get_scheduler_logger(tmp_path)

    assert len(parent.handlers) == handler_count_after_first
