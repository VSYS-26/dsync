"""CLI command to enable the background sync server daemon."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from dsync.cli.daemon_ops import run_enable
from dsync.config.daemon import DaemonConfig
from dsync.daemon.daemons import ServerDaemon

if TYPE_CHECKING:
    from dsync.state import AppState


def enable(
    ctx: typer.Context,
    port: Annotated[int, typer.Option(help="Network port for daemon")] = 9999,
    cert: Annotated[str, typer.Option(help="Path to TLS certificate")] = "cert.pem",
    key: Annotated[str, typer.Option(help="Path to TLS private key")] = "key.pem",
) -> None:
    """Enable background sync server daemon.

    Installs a platform service (systemd on Linux, launchd on macOS, a Windows
    service) for auto-start on boot and persistent operation; the right one is
    chosen automatically for the current OS. The daemon then listens for incoming
    sync connections from peers.

    Args:
        ctx: Typer context containing AppState
        port: Network port for the daemon to listen on
        cert: Path to TLS certificate file
        key: Path to TLS private key file
    """
    state: AppState = ctx.obj
    daemon = ServerDaemon(state.config_dir, port=port, cert=cert, key=key)
    run_enable(
        daemon,
        label="server",
        save_config=lambda: DaemonConfig(enabled=True, port=port, cert=cert, key=key).save(
            state.config_dir, overwrite=True
        ),
    )
