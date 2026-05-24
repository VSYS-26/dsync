"""CLI command to run a dsync relay server."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

import typer

from dsync.cli.console import error, info, success
from dsync.network.relay_server import RelayServer


def serve(
    ctx: typer.Context,
    host: Annotated[
        str,
        typer.Option(help="UDP bind address (use 0.0.0.0 to accept from anywhere)"),
    ] = "0.0.0.0",
    port: Annotated[
        int,
        typer.Option(help="UDP port to listen on"),
    ] = 9000,
    cert: Annotated[
        str,
        typer.Option(help="Path to the relay's TLS certificate (.pem)"),
    ] = "cert.pem",
    key: Annotated[
        str,
        typer.Option(help="Path to the relay's private key (.pem)"),
    ] = "key.pem",
    log_level: Annotated[
        str,
        typer.Option(help="Logging level (DEBUG | INFO | WARNING | ERROR)"),
    ] = "INFO",
) -> None:
    """Run a pure-rendezvous relay server.

    Listens on ``host:port`` for peer QUIC connections, observes each peer's
    NATted UDP endpoint, and brokers hole-punching when two peers want to
    sync. File bytes never traverse the relay.

    The relay's own fingerprint is derived from its public key — share it
    with peers so they can pin it in their ``relays.yaml``.
    """
    del ctx  # AppState is loaded but the relay doesn't consult it; future PRs may.
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    relay = RelayServer(host=host, port=port, cert_path=cert, key_path=key)
    info(f"Relay fingerprint: {relay.fingerprint}")
    info(f"Binding {host}:{port} ...")

    try:
        asyncio.run(_run_until_stopped(relay))
        success("Relay shut down cleanly.")
    except KeyboardInterrupt:
        info("\nShutting down...")
    except Exception as exc:
        error(f"Relay stopped: {exc}")
        raise typer.Exit(code=1) from exc


async def _run_until_stopped(relay: RelayServer) -> None:
    """Start the relay and block until cancelled."""
    await relay.start()
    info(f"Relay listening on UDP port {relay.bound_port}")
    try:
        # Block forever until the loop is interrupted.
        await asyncio.Event().wait()
    finally:
        await relay.close()
