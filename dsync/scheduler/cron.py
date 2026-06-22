"""Cron evaluation for the scheduler (thin croniter wrapper)."""

from __future__ import annotations

import datetime

from croniter import croniter


def validate_cron(expr: str) -> None:
    """Raise ``ValueError`` if ``expr`` is not a valid cron expression.

    Args:
        expr: The cron expression to validate.

    Raises:
        ValueError: If ``expr`` is not a valid cron expression.
    """
    if not croniter.is_valid(expr):
        raise ValueError(f"invalid cron expression: {expr!r}")


def is_due(
    expr: str,
    last_run: datetime.datetime | None,
    now: datetime.datetime,
) -> bool:
    """Return whether a scheduled fire time has elapsed since ``last_run``.

    Takes the most recent scheduled fire time at or before ``now``. The folder
    is due if it never ran or ``last_run`` predates that fire time. This also
    covers missed runs (device offline): a long-elapsed fire time fires exactly
    once on the next check.

    Args:
        expr: The cron expression.
        last_run: Timestamp of the last successful run, or ``None`` if never run.
        now: The current (timezone-aware, local) time.

    Returns:
        ``True`` if the folder is due to run.
    """
    prev_fire = croniter(expr, now).get_prev(datetime.datetime)
    return last_run is None or last_run < prev_fire
