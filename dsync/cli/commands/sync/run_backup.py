"""CLI command to sync folders: tries local P2P first, falls back to relay daemon.

Local path: discovers peers via mDNS/Zeroconf and connects directly via QUIC.
Relay fallback: if no peer found on LAN (or connection fails), asks the running
relay-connect daemon over a Unix-domain socket to perform the sync.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from pathlib import Path
import socket
from typing import TYPE_CHECKING, Annotated

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import load_pem_private_key
import typer

from dsync.cli.console import error, info, success, warn
from dsync.config import SyncMode
from dsync.identity import PeerMapStore
from dsync.network.discovery import FingerprintAnnouncer, PeerDiscoveryRunner
from dsync.network.local_ipc import LocalControlClient, SyncFolderRequest
from dsync.network.peer_session import PeerSession
from dsync.network.quic_core import build_quic_configuration
from dsync.network.quic_transport import start_dialer

if TYPE_CHECKING:
    from typing import Any

    from dsync.config import FolderEntry, TrustedDevice
    from dsync.state import AppState

_LOCAL_P2P_PORT = 9999


def _get_own_fingerprint(_cert_path: str, key_path: str) -> str | None:
    """Extract fingerprint from the TLS certificate (matches devices.yaml)."""
    try:
        with Path(key_path).open("rb") as f:
            key = load_pem_private_key(f.read(), password=None)
        spki = key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return hashlib.sha256(spki).hexdigest()
    except Exception:
        return None


def sync(
    ctx: typer.Context,
    folder_id: Annotated[
        str | None,
        typer.Option(
            "--folder-id",
            "-f",
            help="Specific folder ID (omit to sync all configured folders)",
        ),
    ] = None,
    peer_host: Annotated[
        str | None,
        typer.Option("--peer-host", help="Peer IP/hostname (bypasses peer discovery)"),
    ] = None,
    discover_timeout: Annotated[
        int,
        typer.Option("--discover-timeout", help="Seconds to wait for LAN peer discovery"),
    ] = 8,
    cert: Annotated[str, typer.Option(help="Path to your certificate")] = "cert.pem",
    key: Annotated[str, typer.Option(help="Path to your private key")] = "key.pem",
) -> None:
    """Sync folders: tries local P2P first, falls back to relay daemon.

    Without --folder-id: syncs all folders from folders.yaml.
    With --folder-id: syncs only the specific folder.

    Only folders with mode 'mirror' or 'backup-to-peer' are sent.
    Folders with mode 'backup-from-peer' are receive-only and are skipped.

    Each folder is synced with all its configured trusted devices.
    """
    state: AppState = ctx.obj

    if not state.folders.entries:
        warn("No folders configured in folders.yaml")
        raise typer.Exit(code=1)
    if not state.devices.trusted_devices:
        warn("No trusted devices configured in devices.yaml")
        raise typer.Exit(code=1)

    if folder_id is None:
        folders_to_sync = list(state.folders.entries)
        info(f"Syncing all {len(folders_to_sync)} configured folder(s)...")
    else:
        folder = next((f for f in state.folders.entries if f.id == folder_id), None)
        if folder is None:
            error(f"Folder '{folder_id}' not found in folders.yaml")
            raise typer.Exit(code=1)
        folders_to_sync = [folder]
        info(f"Syncing folder '{folder_id}'...")

    peer_map: dict[str, Any] = {}
    if not peer_host:
        peer_map = _auto_discover_peers(PeerMapStore(), discover_timeout, cert, key)

    total_syncs, failed_syncs = asyncio.run(
        _sync_all_folders(folders_to_sync, peer_map, peer_host, cert, key, state)
    )

    info(f"\n{'=' * 60}")
    if failed_syncs > 0:
        warn(f"Completed: {total_syncs} successful, {failed_syncs} failed")
        raise typer.Exit(code=1)
    success(f"Completed: {total_syncs} successful sync(s)")


def _auto_discover_peers(
    peer_store: PeerMapStore,
    timeout: int,
    cert: str = "cert.pem",
    key: str = "key.pem",
) -> dict[str, Any]:
    """Announce our fingerprint and discover matching peers on the LAN."""
    own_fingerprint = _get_own_fingerprint(cert, key)
    if own_fingerprint:
        info(f"Announcing as {own_fingerprint[:16]}...")
    else:
        warn("No cert/key found, own fingerprint filtering disabled.")

    announcer = FingerprintAnnouncer(fingerprint=own_fingerprint or "")
    announcer.start()

    runner = PeerDiscoveryRunner(store=peer_store)
    info("Discovering peer(s) on local network...")
    peers, stats = runner.discover(
        duration_seconds=timeout,
        own_fingerprint=own_fingerprint,
    )
    announcer.stop()

    info(f"Discovery complete: {stats.events_seen} events, {stats.peers_written} peers total")
    return peers


async def _sync_all_folders(
    folders_to_sync: list[FolderEntry],
    peer_map: dict[str, Any],
    peer_host: str | None,
    cert: str,
    key: str,
    state: AppState,
) -> tuple[int, int]:
    """Sync all folders with their peers. Returns (total_syncs, failed_syncs)."""
    total_syncs = 0
    failed_syncs = 0

    for idx, folder in enumerate(folders_to_sync, start=1):
        if folder.mode == SyncMode.BACKUP_FROM_PEER:
            info(
                f"[{idx}/{len(folders_to_sync)}] Folder: {folder.id} - SKIPPED"
                " (mode backup-from-peer: receive only)"
            )
            continue
        if folder.mode not in (SyncMode.MIRROR, SyncMode.BACKUP_TO_PEER):
            warn(f"Unknown mode for folder {folder.id}, skipping")
            continue

        if not folder.devices:
            warn(
                f"[{idx}/{len(folders_to_sync)}] Folder: {folder.id} - SKIPPED"
                " (no devices configured)"
            )
            continue

        info(f"\n[{idx}/{len(folders_to_sync)}] Folder: {folder.id}")
        info(f"    Path: {folder.path}")
        info(f"    Mode: {folder.mode.value}")
        info(f"    Peers: {', '.join(folder.devices)}")

        sync_tasks = []
        peer_ids = []

        for peer_id in folder.devices:
            peer_device = _find_device(state, peer_id)
            if peer_device is None:
                error(f"Peer device {peer_id} not found in devices.yaml")
                failed_syncs += 1
                continue

            sync_tasks.append(
                _sync_folder_with_peer(
                    folder=folder,
                    peer_id=peer_id,
                    peer_fingerprint=peer_device.fingerprint,
                    peer_map=peer_map,
                    peer_host=peer_host,
                    cert=cert,
                    key=key,
                    state=state,
                )
            )
            peer_ids.append(peer_id)

        results = await asyncio.gather(*sync_tasks, return_exceptions=True)

        for peer_id, result in zip(peer_ids, results, strict=False):
            if isinstance(result, Exception):
                error(f"Failed to sync with peer {peer_id}: {result}")
                failed_syncs += 1
            else:
                total_syncs += 1

    return total_syncs, failed_syncs


def _find_device(state: AppState, device_id: str) -> TrustedDevice | None:
    """Find a device entry by its ID."""
    for device in state.devices.trusted_devices:
        if device.id == device_id:
            return device
    return None


async def _sync_folder_with_peer(
    folder: FolderEntry,
    peer_id: str,
    peer_fingerprint: str,
    peer_map: dict[str, Any],
    peer_host: str | None,
    cert: str,
    key: str,
    state: AppState,
) -> None:
    """Sync a folder with a peer: try local P2P first, fall back to relay.

    Args:
        folder: The folder configuration from folders.yaml.
        peer_id: The device ID of the peer.
        peer_fingerprint: The peer's public key fingerprint.
        peer_map: Dictionary of fingerprint -> peer info from LAN discovery.
        peer_host: Direct peer IP/hostname override (bypasses discovery).
        cert: Path to local certificate file.
        key: Path to local private key file.
        state: Application state.
    """
    peer_ip: str | None
    if peer_host:
        peer_ip = peer_host
    else:
        peer_info = peer_map.get(peer_fingerprint)
        peer_ip = peer_info.ipv4 if peer_info is not None else None

    if peer_ip is not None:
        info(f"  [{peer_id}] Connecting directly at {peer_ip}:{_LOCAL_P2P_PORT}...")
        try:
            await _sync_direct_quic(folder, peer_ip, _LOCAL_P2P_PORT, cert, key, state)
        except Exception as exc:
            warn(f"  [{peer_id}] Local P2P failed ({exc}), trying relay...")
        else:
            success(f"  [{peer_id}] synced via local P2P")
            return

    # Relay fallback via IPC daemon
    try:
        client = LocalControlClient.discover()
    except FileNotFoundError:
        raise RuntimeError(
            f"Peer {peer_id} not reachable locally and relay daemon not running. "
            "Run `dsync relay connect <relay-id>` first."
        ) from None

    response = await client.request(SyncFolderRequest(folder_id=folder.id, peer_id=peer_id))
    if response.status != "ok":
        raise RuntimeError(f"Relay sync failed: {response.reason}")
    success(f"  [{peer_id}] synced via relay")


async def _sync_direct_quic(
    folder: FolderEntry,
    peer_ip: str,
    peer_port: int,
    cert: str,
    key: str,
    state: AppState,
) -> None:
    """Direct QUIC sync: dial peer and send folder via PeerSession."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))  # nosec B104
    sock.setblocking(False)

    cfg = build_quic_configuration(is_client=True, cert_path=cert, key_path=key)
    endpoint = await start_dialer(sock=sock, peer_addr=(peer_ip, peer_port), configuration=cfg)
    try:
        await asyncio.wait_for(endpoint.protocol.wait_connected(), timeout=15.0)
        reader, writer = await endpoint.protocol.create_stream()

        session = PeerSession.as_source(cert_path=cert, key_path=key, state=state, folder=folder)
        await session.run(reader, writer, endpoint.protocol._quic)
    finally:
        with contextlib.suppress(Exception):
            endpoint.transport.close()
        sock.close()
