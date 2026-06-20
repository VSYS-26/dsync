"""CLI command to check the background sync server daemon status."""

from __future__ import annotations

from typing import TYPE_CHECKING

# Runtime import: Typer resolves the context type hint at registration.
import typer  # noqa: TC002

from dsync.cli.console import info
from dsync.cli.daemon_ops import run_status
from dsync.daemon.daemons import ServerDaemon

if TYPE_CHECKING:
    from dsync.state import AppState


def status(ctx: typer.Context) -> None:
    """Check sync server daemon status.

    Shows whether the daemon is enabled and if it's currently running.

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

    def _extra() -> None:
        info(f"Port: {state.daemon.port}")
        info(f"Config Dir: {state.config_dir}")

    run_status(daemon, label="server", extra_info=_extra)
