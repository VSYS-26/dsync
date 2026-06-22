"""CLI command to disable the background interval-sync scheduler daemon."""

from __future__ import annotations

from typing import TYPE_CHECKING

# Runtime import: Typer resolves the context type hint at registration.
import typer

from dsync.cli.daemon_ops import run_disable
from dsync.config.scheduler import SchedulerConfig
from dsync.daemon.daemons import SchedulerDaemon

if TYPE_CHECKING:
    from dsync.state import AppState


def disable(ctx: typer.Context) -> None:
    """Disable the interval-sync scheduler daemon.

    Stops the platform service (systemd/launchd/Windows) and removes it from
    auto-start.

    Args:
        ctx: Typer context containing AppState.
    """
    state: AppState = ctx.obj
    cfg = SchedulerConfig.load(state.config_dir)
    daemon = SchedulerDaemon(state.config_dir, cert=cfg.cert, key=cfg.key)
    run_disable(
        daemon,
        label="scheduler",
        save_config=lambda: SchedulerConfig(enabled=False).save(state.config_dir, overwrite=True),
    )
