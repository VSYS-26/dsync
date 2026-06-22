import datetime

import pytest

from dsync.scheduler.cron import is_due, validate_cron

_TZ = datetime.UTC


def test_validate_cron_accepts_valid_expression() -> None:
    validate_cron("*/30 * * * *")


def test_validate_cron_rejects_invalid_expression() -> None:
    with pytest.raises(ValueError, match="invalid cron expression"):
        validate_cron("not-a-cron")


def test_is_due_when_never_run() -> None:
    now = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=_TZ)
    assert is_due("* * * * *", None, now) is True


def test_is_due_when_last_run_before_prev_fire() -> None:
    now = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=_TZ)
    last_run = datetime.datetime(2026, 1, 1, 10, 30, tzinfo=_TZ)
    assert is_due("0 * * * *", last_run, now) is True


def test_is_due_false_when_last_run_after_prev_fire() -> None:
    now = datetime.datetime(2026, 1, 1, 12, 30, tzinfo=_TZ)
    last_run = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=_TZ)
    assert is_due("0 * * * *", last_run, now) is False
