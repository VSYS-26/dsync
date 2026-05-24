"""CLI command: open a long-running relay control channel.

``dsync relay connect <id>`` keeps a QUIC connection to the named relay
open, accepts incoming peer-to-peer dials brokered by the relay, and
hosts a local IPC socket so ``dsync sync run_backup`` can request
outbound syncs. The daemon auto-reconnects with exponential backoff if
the control channel drops; an app-layer ``CONTROL_PING`` every 15 s
keeps the NAT mapping warm.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from dsync.cli.console import error, info, success, warn
from dsync.network.errors import (
    RelayAuthError,
    RelayError,
    RelayProtocolError,
)
from dsync.network.relay_daemon import RelayDaemon

if TYPE_CHECKING:
    from dsync.state import AppState


def connect(
    ctx: typer.Context,
    relay_id: Annotated[
        str,
        typer.Argument(help="Relay id from relays.yaml to connect to"),
    ],
    cert: Annotated[
        str,
        typer.Option(help="Path to your TLS certificate (.pem)"),
    ] = "cert.pem",
    key: Annotated[
        str,
        typer.Option(help="Path to your private key (.pem)"),
    ] = "key.pem",
    recv_dir: Annotated[
        Path,
        typer.Option(
            "--recv-dir",
            help="Directory where incoming files are written, under <peer-id>/.",
        ),
    ] = Path("received-files"),
    log_level: Annotated[
        str,
        typer.Option(help="Logging level (DEBUG | INFO | WARNING | ERROR)"),
    ] = "INFO",
) -> None:
    """Connect to a relay and host the local IPC socket until interrupted."""
    state: AppState = ctx.obj
    relay = next((r for r in state.relays.relays if r.id == relay_id), None)
    if relay is None:
        error(f"Relay '{relay_id}' is not configured in relays.yaml")
        raise typer.Exit(code=1)

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    recv_dir.mkdir(parents=True, exist_ok=True)
    daemon = RelayDaemon(
        relay=relay,
        cert_path=cert,
        key_path=key,
        state=state,
        recv_dir=recv_dir,
    )

    info(f"Connecting to relay '{relay.id}' at {relay.host}:{relay.port}...")
    try:
        asyncio.run(_run_until_stopped(daemon))
        success("Relay connection closed cleanly.")
    except KeyboardInterrupt:
        warn("\nShutting down...")
    except RelayAuthError as exc:
        error(f"Relay authentication failed: {exc}")
        error(
            "Hint: the relay's TLS certificate fingerprint does not match the "
            "`fingerprint` field in your relays.yaml for this relay id. "
            "Re-issue the entry or fix the pin."
        )
        raise typer.Exit(code=1) from exc
    except (ConnectionRefusedError, OSError) as exc:
        error(f"Could not reach relay at {relay.host}:{relay.port}: {exc}")
        error(
            "Hint: make sure `dsync relay serve` is running on that host:port "
            "and that no firewall is blocking the UDP port."
        )
        raise typer.Exit(code=1) from exc
    except (RelayProtocolError, RelayError) as exc:
        error(f"Relay protocol error: {exc}")
        raise typer.Exit(code=1) from exc
    except FileNotFoundError as exc:
        error(f"Cert or key file not found: {exc}")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        error(f"Daemon stopped: {exc}")
        raise typer.Exit(code=1) from exc


async def _run_until_stopped(daemon: RelayDaemon) -> None:
    """Start the daemon and wait until it shuts down."""
    await daemon.start()
    try:
        await daemon.wait_until_shutdown()
    finally:
        await daemon.close()
