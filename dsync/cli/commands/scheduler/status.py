"""CLI command to check the interval-sync scheduler daemon status."""

from __future__ import annotations

from typing import TYPE_CHECKING

# Runtime import: Typer resolves the context type hint at registration.
import typer  # noqa: TC002

from dsync.cli.console import info
from dsync.cli.daemon_ops import run_status
from dsync.config.scheduler import SchedulerConfig
from dsync.daemon.daemons import SchedulerDaemon

if TYPE_CHECKING:
    from dsync.state import AppState


def status(ctx: typer.Context) -> None:
    """Check the interval-sync scheduler daemon status.

    Shows whether the daemon is enabled and if it is currently running.

    Args:
        ctx: Typer context containing AppState.
    """
    state: AppState = ctx.obj
    cfg = SchedulerConfig.load(state.config_dir)
    daemon = SchedulerDaemon(state.config_dir, cert=cfg.cert, key=cfg.key)

    def _extra() -> None:
        info(f"Config Dir: {state.config_dir}")

    run_status(daemon, label="scheduler", extra_info=_extra)
