"""CLI command to disable the background sync server daemon."""

from __future__ import annotations

from typing import TYPE_CHECKING

# Runtime import: Typer resolves the context type hint at registration.
import typer

from dsync.cli.daemon_ops import run_disable
from dsync.config.daemon import DaemonConfig
from dsync.daemon.daemons import ServerDaemon

if TYPE_CHECKING:
    from dsync.state import AppState


def disable(ctx: typer.Context) -> None:
    """Disable background sync server daemon.

    Stops the platform service (systemd/launchd/Windows) and removes it from
    auto-start. The daemon will no longer run in the background.

    Args:
        ctx: Typer context containing AppState
    """
    state: AppState = ctx.obj
    daemon = ServerDaemon(
        state.config_dir,
        port=state.daemon.port,
        cert=state.daemon.cert,
        key=state.daemon.key,
    )
    run_disable(
        daemon,
        label="server",
        save_config=lambda: DaemonConfig(enabled=False).save(state.config_dir, overwrite=True),
    )
