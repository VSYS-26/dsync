"""CLI command to manually sync folders with their configured peers."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import load_pem_private_key
import typer

from dsync.cli.console import error, info, success, warn
from dsync.config import SyncMode
from dsync.identity import PeerMapStore
from dsync.network.discovery import FingerprintAnnouncer, PeerDiscoveryRunner
from dsync.network.node import P2PNode

if TYPE_CHECKING:
    from typing import Any

    from dsync.config import FolderEntry, TrustedDevice
    from dsync.state import AppState


def _get_own_fingerprint(cert_path: str, key_path: str) -> str | None:  # noqa: ARG001
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
        str | None, typer.Option("--folder-id", "-f", help="Specific folder ID (or sync all)")
    ] = None,
    peer_host: Annotated[
        str | None,
        typer.Option("--peer-host", help="Peer IP/hostname (bypasses peer map)"),
    ] = None,
    discover_timeout: Annotated[
        int,
        typer.Option("--discover-timeout", help="Seconds to wait for peer discovery"),
    ] = 8,
    map_file: Annotated[Path, typer.Option("--map-file", help="Path to peer map JSON file")] = Path(
        ".dsync"
    )
    / "peer-map.json",
    cert: Annotated[str, typer.Option(help="Path to your certificate")] = "cert.pem",
    key: Annotated[str, typer.Option(help="Path to your private key")] = "key.pem",
) -> None:
    """Manually sync folders with their configured peers.

    Without --folder-id: syncs all folders from folders.yaml.
    With --folder-id: syncs only the specific folder.

    Only folders with mode 'mirror' or 'backup-to-peer' are sent.
    Folders with mode 'backup-from-peer' are receive-only and are skipped
    by this send command.

    Each folder is synced with all its configured trusted devices.
    """
    state: AppState = ctx.obj

    if not state.folders.entries:
        warn("No folders configured in folders.yaml")
        raise typer.Exit(code=1)

    if not state.devices.trusted_devices:
        warn("No trusted devices configured in devices.yaml")
        raise typer.Exit(code=1)

    # Load peer map
    peer_store = PeerMapStore(file_path=map_file)
    peer_map = peer_store.list_peers()

    # Auto-discover peers if no explicit host given
    if not peer_host:
        peer_map = _auto_discover_peers(peer_store, discover_timeout, cert, key)

    if not peer_map and not peer_host:
        warn("Peer map is empty. Run 'dsync peer discover' first, or use --peer-host.")
        raise typer.Exit(code=1)

    # Choose which folders should be synced
    if folder_id is None:
        folders_to_sync = state.folders.entries
        info(f"Syncing all {len(folders_to_sync)} configured folder(s)...")
    else:
        folder = _find_folder(state, folder_id)
        if folder is None:
            error(f"Folder '{folder_id}' not found in folders.yaml")
            raise typer.Exit(code=1)
        folders_to_sync = [folder]
        info(f"Syncing folder '{folder_id}'...")

    # Run async sync
    total_syncs, failed_syncs = asyncio.run(
        _sync_all_folders(folders_to_sync, peer_map, peer_host, cert, key, state)
    )

    info(f"\n{'=' * 60}")
    success(f"Completed: {total_syncs} successful sync(s)")
    if failed_syncs > 0:
        warn(f"Failed: {failed_syncs} sync(s)")


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
        # Mode filter: only send mirror and backup-to-peer
        if folder.mode == SyncMode.BACKUP_FROM_PEER:
            info(
                f"[{idx}/{len(folders_to_sync)}] Folder: {folder.id} - SKIPPED (mode backup-from-peer: receive only)"
            )
            continue
        if folder.mode not in (SyncMode.MIRROR, SyncMode.BACKUP_TO_PEER):
            warn(f"Unknown mode for folder {folder.id}, skipping")
            continue

        if not folder.devices:
            warn(
                f"[{idx}/{len(folders_to_sync)}] Folder: {folder.id} - SKIPPED (no devices configured)"
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
                success(f"Synced with peer {peer_id}")
                total_syncs += 1

    return total_syncs, failed_syncs


def _find_folder(state: AppState, folder_id: str) -> FolderEntry | None:
    """Find a folder entry by its ID."""
    for folder in state.folders.entries:
        if folder.id == folder_id:
            return folder
    return None


def _find_device(state: AppState, device_id: str) -> TrustedDevice | None:
    """Find a device entry by its ID."""
    for device in state.devices.trusted_devices:
        if device.id == device_id:
            return device
    return None


async def _sync_folder_with_peer(
    folder: FolderEntry,
    peer_fingerprint: str,
    peer_map: dict[str, Any],
    peer_host: str | None,
    cert: str,
    key: str,
    state: AppState,
) -> None:
    """Perform the actual sync operation between a folder and a peer.

    Args:
        folder: The folder configuration from folders.yaml
        peer_fingerprint: The peer's public key fingerprint
        peer_map: Dictionary of fingerprint -> peer info mappings
        peer_host: Direct peer IP/hostname (bypasses peer_map if given)
        cert: Path to local certificate file
        key: Path to local private key file
        state: Application state

    Raises:
        ValueError: If peer not found in peer map and no peer_host given
        Exception: If sync fails
    """
    # Resolve peer IP: either from --peer-host or from peer map
    if peer_host:
        peer_ip = peer_host
    else:
        if peer_fingerprint not in peer_map:
            raise ValueError(f"Peer {peer_fingerprint} not found in peer map")
        peer_info = peer_map[peer_fingerprint]
        peer_ip = peer_info.ipv4

    info(f"Connecting to {peer_fingerprint[:16]}... at {peer_ip}")

    # Create P2PNode as client
    node = P2PNode(
        is_server=False,
        cert_path=cert,
        key_path=key,
        state=state,
        folder=folder,
    )

    port = 9999

    try:
        await node.start(host=peer_ip, port=port)
    except ConnectionRefusedError:
        raise ConnectionError(
            f"Could not connect to {peer_ip}:{port} - peer may be offline"
        ) from None
    except Exception as e:
        raise RuntimeError(f"Sync failed: {e}") from e
