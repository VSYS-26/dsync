"""CLI command: direct QUIC peer-to-peer sync (server or client mode)."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
import socket
from typing import TYPE_CHECKING, Annotated

import typer

from dsync.cli.console import error, info, success, warn
from dsync.network.discovery import FingerprintAnnouncer, PeerDiscoveryRunner
from dsync.network.peer_session import PeerSession
from dsync.network.quic_core import build_quic_configuration
from dsync.network.quic_transport import start_dialer, start_listener

if TYPE_CHECKING:
    from dsync.config import FolderEntry
    from dsync.identity import PeerMapStore
    from dsync.state import AppState

_DEFAULT_PORT = 9999


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
    port: Annotated[int, typer.Option(help="UDP port for direct QUIC connection")] = _DEFAULT_PORT,
    recv_dir: Annotated[
        Path,
        typer.Option("--recv-dir", help="Fallback directory for received files"),
    ] = Path("received-files"),
) -> None:
    """Direct QUIC peer-to-peer sync.

    Server mode: listens for one inbound connection and receives files.
    Client mode: connects to a peer and sends the configured folder.
    """
    state: AppState = ctx.obj
    is_server = mode.lower() == "server"

    if is_server:
        announcer = None
        try:
            from dsync.cli.commands.sync.run_backup import _get_own_fingerprint
            from dsync.network.discovery import FingerprintAnnouncer

            fp = _get_own_fingerprint(cert, key)
            if fp:
                announcer = FingerprintAnnouncer(fingerprint=fp)
                announcer.start()
                info(f"Announcing as {fp[:16]}...")
        except Exception:
            pass

        try:
            asyncio.run(_run_server(state, cert, key, port, recv_dir))
            success("Transfer complete.")
        except KeyboardInterrupt:
            warn("\nShutting down...")
        finally:
            if announcer is not None:
                announcer.stop()
    else:
        folder = _resolve_folder(state, folder_id)
        if folder is None:
            error("--folder-id required in client mode (or no matching folder found)")
            raise typer.Exit(code=1)
        peer_ip = host or _discover_peer_by_id(state, peer, cert, key)
        try:
            asyncio.run(_run_client(state, cert, key, peer_ip, port, folder))
            success(f"Folder '{folder.id}' synced.")
        except KeyboardInterrupt:
            warn("\nShutting down...")
        except Exception as exc:
            error(f"Sync failed: {exc}")
            raise typer.Exit(code=1) from exc


async def _run_server(
    state: AppState,
    cert: str,
    key: str,
    port: int,
    recv_dir: Path,
) -> None:
    """QUIC listener: accept one inbound connection and receive files."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))  # nosec B104
    sock.setblocking(False)

    cfg = build_quic_configuration(is_client=False, cert_path=cert, key_path=key)

    loop = asyncio.get_running_loop()
    stream_future: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = (
        loop.create_future()
    )

    def on_stream(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        if not stream_future.done():
            stream_future.set_result((reader, writer))

    recv_dir.mkdir(parents=True, exist_ok=True)
    info(f"Listening on UDP :{port} (waiting for peer)...")

    endpoint = await start_listener(sock=sock, configuration=cfg, stream_handler=on_stream)
    try:
        accepted = await asyncio.wait_for(endpoint.wait_accepted(), timeout=120.0)
        await asyncio.wait_for(accepted.wait_connected(), timeout=15.0)
        reader, writer = await asyncio.wait_for(stream_future, timeout=15.0)

        session = PeerSession.as_peer(cert_path=cert, key_path=key, state=state, recv_dir=recv_dir)
        peer_id = await session.run(reader, writer, accepted._quic)
        info(f"Received files from {peer_id}")
    finally:
        with contextlib.suppress(Exception):
            endpoint.transport.close()
        sock.close()


async def _run_client(
    state: AppState,
    cert: str,
    key: str,
    peer_ip: str,
    port: int,
    folder: FolderEntry,
) -> None:
    """QUIC dialer: connect to peer and send folder."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))  # nosec B104
    sock.setblocking(False)

    cfg = build_quic_configuration(is_client=True, cert_path=cert, key_path=key)

    info(f"Connecting to {peer_ip}:{port}...")
    endpoint = await start_dialer(sock=sock, peer_addr=(peer_ip, port), configuration=cfg)
    try:
        await asyncio.wait_for(endpoint.protocol.wait_connected(), timeout=15.0)
        reader, writer = await endpoint.protocol.create_stream()

        session = PeerSession.as_source(cert_path=cert, key_path=key, state=state, folder=folder)
        peer_id = await session.run(reader, writer, endpoint.protocol._quic)
        info(f"Sent folder '{folder.id}' to {peer_id}")
    finally:
        with contextlib.suppress(Exception):
            endpoint.transport.close()
        sock.close()


def _resolve_folder(state: AppState, folder_id: str | None) -> FolderEntry | None:
    if folder_id is None:
        return None
    return next((f for f in state.folders.entries if f.id == folder_id), None)


def _discover_peer_by_id(
    state: AppState,
    device_id: str | None,
    cert: str = "cert.pem",
    key: str = "key.pem",
) -> str:
    from dsync.cli.commands.sync.run_backup import _get_own_fingerprint

    if device_id is None:
        warn("No peer specified, falling back to localhost")
        return "127.0.0.1"

    target_device = next((d for d in state.devices.trusted_devices if d.id == device_id), None)
    if target_device is None:
        error(f"Device '{device_id}' not found in devices.yaml")
        raise typer.Exit(code=1)

    own_fp = _get_own_fingerprint(cert, key)
    if own_fp:
        info(f"Announcing as {own_fp[:16]}...")
    else:
        warn("No cert/key found, own fingerprint filtering disabled.")

    announcer = FingerprintAnnouncer(fingerprint=own_fp or "")
    announcer.start()

    store_instance = _make_peer_store()
    runner = PeerDiscoveryRunner(store=store_instance)
    info("Discovering peer on local network...")
    peers, stats = runner.discover(duration_seconds=8, own_fingerprint=own_fp)
    announcer.stop()

    info(f"Discovery: {stats.events_seen} events, {stats.peers_written} peers")

    peer_info = peers.get(target_device.fingerprint)
    if peer_info is None:
        error(f"Peer '{device_id}' not found on local network")
        raise typer.Exit(code=1)

    success(f"Found {device_id}")
    return str(peer_info.ipv4)


def _make_peer_store() -> PeerMapStore:
    from dsync.identity import PeerMapStore

    return PeerMapStore()
