"""CLI command to enable the background interval-sync scheduler daemon."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from dsync.cli.daemon_ops import run_enable
from dsync.config.scheduler import SchedulerConfig
from dsync.daemon.daemons import SchedulerDaemon

if TYPE_CHECKING:
    from dsync.state import AppState


def enable(
    ctx: typer.Context,
    cert: Annotated[str, typer.Option(help="Path to TLS certificate")] = "cert.pem",
    key: Annotated[str, typer.Option(help="Path to TLS private key")] = "key.pem",
) -> None:
    """Enable the interval-sync scheduler daemon.

    Installs a platform service (systemd on Linux, launchd on macOS, a Windows
    service) that runs scheduled folder syncs in the background; the right one
    is chosen automatically for the current OS.

    Args:
        ctx: Typer context containing AppState.
        cert: Path to TLS certificate file.
        key: Path to TLS private key file.
    """
    state: AppState = ctx.obj
    daemon = SchedulerDaemon(state.config_dir, cert=cert, key=key)
    run_enable(
        daemon,
        label="scheduler",
        save_config=lambda: SchedulerConfig(enabled=True, cert=cert, key=key).save(
            state.config_dir, overwrite=True
        ),
    )
