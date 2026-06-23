"""CLI command to run the scheduler loop in the foreground."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated

import typer

from dsync.cli.console import welcome
from dsync.scheduler.runner import SchedulerRunner

if TYPE_CHECKING:
    from dsync.state import AppState


def run(
    ctx: typer.Context,
    cert: Annotated[str, typer.Option(help="Path to TLS certificate")] = "cert.pem",
    key: Annotated[str, typer.Option(help="Path to TLS private key")] = "key.pem",
) -> None:
    """Run the interval-sync scheduler loop in the foreground.

    This is the payload the scheduler daemon executes. It polls folder configs
    and runs folders whose cron schedule is due, using the same code path as a
    manual ``dsync sync run``.

    Args:
        ctx: Typer context containing AppState.
        cert: Path to TLS certificate file.
        key: Path to TLS private key file.
    """
    state: AppState = ctx.obj
    welcome(role="scheduler daemon")
    asyncio.run(SchedulerRunner(state.config_dir, cert=cert, key=key).run())
