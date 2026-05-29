"""CLI command to manually sync folders with their configured peers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from dsync.cli.console import error, info, success, warn
from dsync.config import SyncMode
from dsync.identity import PeerMapStore
from dsync.network.node import P2PNode

if TYPE_CHECKING:
    from typing import Any

    from dsync.config import FolderEntry, TrustedDevice
    from dsync.state import AppState


def sync(
    ctx: typer.Context,
    folder_id: Annotated[
        str | None, typer.Option("--folder-id", "-f", help="Specific folder ID (or sync all")
    ] = None,
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

    if not peer_map:
        warn("Peer map is empty. Run 'dsync peer discover' first.")
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
        _sync_all_folders(folders_to_sync, peer_map, cert, key, state)
    )

    info(f"\n{'=' * 60}")
    success(f"Completed: {total_syncs} successful sync(s)")
    if failed_syncs > 0:
        warn(f"Failed: {failed_syncs} sync(s)")


async def _sync_all_folders(
    folders_to_sync: list[FolderEntry],
    peer_map: dict[str, Any],
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
    cert: str,
    key: str,
    state: AppState,
) -> None:
    """Perform the actual sync operation between a folder and a peer.

    Args:
        folder: The folder configuration from folders.yaml
        peer_fingerprint: The peer's public key fingerprint
        peer_map: Dictionary of fingerprint -> peer info mappings
        cert: Path to local certificate file
        key: Path to local private key file
        state: Application state

    Raises:
        ValueError: If peer not found in peer map
        Exception: If sync fails
    """
    # Find peer in map
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
