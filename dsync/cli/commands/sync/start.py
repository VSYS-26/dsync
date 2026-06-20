"""CLI command to start peer-to-peer data synchronization."""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Annotated

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import load_pem_private_key
import typer

from dsync.cli.console import error, info, success, warn
from dsync.identity import PeerMapStore
from dsync.network.discovery import FingerprintAnnouncer, PeerDiscoveryRunner
from dsync.network.node import P2PNode

if TYPE_CHECKING:
    from dsync.config import FolderEntry
    from dsync.state import AppState


def _get_own_fingerprint(cert_path: str, key_path: str) -> str | None:  # noqa: ARG001
    """Extract fingerprint from the TLS certificate (matches devices.yaml)."""
    try:
        from pathlib import Path

        with Path(key_path).open("rb") as f:
            key = load_pem_private_key(f.read(), password=None)
        spki = key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return hashlib.sha256(spki).hexdigest()
    except Exception:
        return None


def start_p2p_sync(
    ctx: typer.Context,
    mode: Annotated[str, typer.Option(help="'server' (waits) or 'client' (connects)")] = "client",
    host: Annotated[
        str | None,
        typer.Option(help="Peer IP/hostname to connect to (client mode only)"),
    ] = None,
    folder_id: Annotated[
        str | None,
        typer.Option("--folder-id", "-f", help="Folder ID to sync (client mode only)"),
    ] = None,
    peer: Annotated[
        str | None,
        typer.Option("--peer", help="Device ID from devices.yaml to connect to (client mode)"),
    ] = None,
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
        host (str | None): Peer IP/hostname for client mode (default: 127.0.0.1).
        folder_id (str | None): Folder ID to sync in client mode.
        peer (str | None): Device ID from devices.yaml to connect to.
        cert (str): Path to ones own TLS certificate file (.pem).
        key (str): Path to ones own private key file (.pem).
        port (int): The network port for direct connections (local).
    """
    state: AppState = ctx.obj

    is_server: bool = mode.lower() == "server"

    # Resolve folder for client mode
    client_folder: FolderEntry | None = None
    peer_device_id: str | None = peer
    if not is_server:
        if folder_id:
            folder = _find_folder(state, folder_id)
            if folder is None:
                error(f"Folder '{folder_id}' not found in folders.yaml")
                raise typer.Exit(code=1)
            client_folder = folder
            info(f"Client mode: syncing folder '{folder_id}'")
            # Auto-infer peer device from folder config if not explicitly given
            if peer_device_id is None and folder.devices:
                peer_device_id = folder.devices[0]
                info(f"Auto-inferred peer device '{peer_device_id}' from folder config")
        else:
            warn("No --folder-id set; client can authenticate but won't send files")

    node = P2PNode(is_server, cert, key, state, folder=client_folder)

    announcer = None
    if is_server:
        host_val = "0.0.0.0"  # nosec B104
        info(f"Starting server on port {port}, waiting for connection...")
        server_fingerprint = _get_own_fingerprint(cert, key)
        if server_fingerprint:
            announcer = FingerprintAnnouncer(fingerprint=server_fingerprint)
            announcer.start()
            info(f"Announcing as {server_fingerprint[:16]}...")
    else:
        host_val = host or _discover_peer_by_id(state, peer_device_id, cert, key)
        info(f"Connecting to server on {host_val}:{port}...")

    try:
        asyncio.run(node.start(host_val, port))
        success("Data transfer completed or connection closed.")
    except KeyboardInterrupt:
        info("\nShutting down...")
    except Exception as e:
        info(f"\n[!] Sync stopped: {e}")
    finally:
        if announcer is not None:
            announcer.stop()


def _discover_peer_by_id(
    state: AppState, device_id: str | None, cert: str = "cert.pem", key: str = "key.pem"
) -> str:
    """Discover a peer's IP on the LAN by its device ID from devices.yaml.

    Falls back to 127.0.0.1 if no device_id given.
    """
    if device_id is None:
        warn("No peer specified, falling back to localhost")
        return "127.0.0.1"

    # Look up the device's fingerprint
    target_device = None
    for device in state.devices.trusted_devices:
        if device.id == device_id:
            target_device = device
            break

    if target_device is None:
        error(f"Device '{device_id}' not found in devices.yaml")
        raise typer.Exit(code=1)

    target_fingerprint = target_device.fingerprint

    # Load our own fingerprint from the TLS certificate
    own_fingerprint = _get_own_fingerprint(cert, key)
    if own_fingerprint:
        info(f"Announcing as {own_fingerprint[:16]}...")
    else:
        warn("No cert/key found, own fingerprint filtering disabled.")

    # Announce + discover
    announcer = FingerprintAnnouncer(fingerprint=own_fingerprint or "")
    announcer.start()

    peer_store = PeerMapStore()
    runner = PeerDiscoveryRunner(store=peer_store)

    info("Discovering 1 peer(s) on local network...")
    peers, stats = runner.discover(
        duration_seconds=8,
        own_fingerprint=own_fingerprint,
    )
    announcer.stop()

    info(f"Discovery complete: {stats.events_seen} events, {stats.peers_written} peers total")

    # Look for our target peer
    if target_fingerprint in peers:
        peer_info = peers[target_fingerprint]
        success(f"Verified: {device_id}")
        return peer_info.ipv4

    error(f"Peer '{device_id}' ({target_fingerprint[:16]}...) not found on local network")
    raise typer.Exit(code=1)


def _find_folder(state: AppState, folder_id: str) -> FolderEntry | None:
    """Find a folder entry by its ID."""
    for folder in state.folders.entries:
        if folder.id == folder_id:
            return folder
    return None
