"""CLI command to start peer-to-peer data synchronization."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated

import typer

from dsync.cli.console import info, success
from dsync.network.node import P2PNode

if TYPE_CHECKING:
    from dsync.state import AppState


def start_p2p_sync(
    ctx: typer.Context,
    mode: Annotated[str, typer.Option(help="'server' (waits) or 'client' (connects)")] = "client",
    cert: Annotated[str, typer.Option(help="Path to your certificate (.pem)")] = "cert.pem",
    key: Annotated[str, typer.Option(help="Path to your private key (.pem)")] = "key.pem",
    port: Annotated[int, typer.Option(help="The network port")] = 9999,
) -> None:
    """Main CLI command for starting peer-to-peer data synchronization.

    Initializes the P2P node and handles the establishment of the basic (still unencrypted) TCP connection.
    It supports two main modes:

    LAN mode: The program either acts as a listening server or connects directly.

    Once the raw socket connection is established, it is passed to the 'P2PNode',
    which handles the TLS handshake, authentication, and data exchange.

    Args:
        ctx (typer.Context): The typer context containing the runtime AppState.
        mode (str): The role in the direct connection ('server' or 'client').
        cert (str): Path to ones own TLS certificate file (.pem).
        key (str): Path to ones own private key file (.pem).
        port (int): The network port for direct connections (local).
    """
    state: AppState = ctx.obj

    is_server: bool = mode.lower() == "server"
    node = P2PNode(is_server, cert, key, state)

    host = "0.0.0.0" if is_server else "127.0.0.1"
    # LAN mode
    if is_server:
        info(f"Starting server on port {port}, waiting for connection...")

    else:
        info(f"Connecting to server on 127.0.0.1:{port}...")

    try:
        asyncio.run(node.start(host, port))
        success("Data transfer completed or connection closed.")
    except KeyboardInterrupt:
        info("\nShutting down...")
    except Exception as e:
        info(f"\n[!] Sync stopped: {e}")
