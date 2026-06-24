"""Peer folder-config compatibility checks run before file transfer.

After AUTH and config exchange, each side verifies the other's folder entry
is a legal counterpart. A mismatch means the two sides disagree on sync
direction — continuing would move data the wrong way or accept it from an
unauthorized device.
"""

from __future__ import annotations

from dsync.config import FolderEntry, SyncMode
from dsync.network.errors import PeerAuthError

_COMPLEMENTARY_MODES: dict[SyncMode, SyncMode] = {
    SyncMode.MIRROR: SyncMode.MIRROR,
    SyncMode.BACKUP_TO_PEER: SyncMode.BACKUP_FROM_PEER,
}


def validate_peer_folder_config(
    local_entry: FolderEntry,
    remote_entry: FolderEntry,
    source_peer_id: str,
) -> None:
    """Verify ``remote_entry`` is a legal counterpart to ``local_entry``.

    Args:
        local_entry: This node's own folder config for the id.
        remote_entry: The sending peer's config for the same id.
        source_peer_id: Trusted-device id of the sender (checked against
            the local ``devices`` whitelist).

    Raises:
        PeerAuthError: If the configurations contradict each other.
    """
    if remote_entry.mode == SyncMode.BACKUP_FROM_PEER:
        raise PeerAuthError(
            f"Peer attempted to send in backup-from-peer mode for '{remote_entry.id}'"
            " — invalid direction"
        )

    expected_local_mode = _COMPLEMENTARY_MODES.get(remote_entry.mode)
    if expected_local_mode is None:
        raise PeerAuthError(
            f"Unsupported remote mode for '{remote_entry.id}': {remote_entry.mode.value}"
        )
    if local_entry.mode != expected_local_mode:
        raise PeerAuthError(
            f"Mode mismatch for '{remote_entry.id}': peer={remote_entry.mode.value}, "
            f"local={local_entry.mode.value} (expected local={expected_local_mode.value})"
        )

    if local_entry.devices is not None and source_peer_id not in local_entry.devices:
        raise PeerAuthError(
            f"Source '{source_peer_id}' is not whitelisted in local folder"
            f" '{remote_entry.id}' devices"
        )

    if local_entry.recursive != remote_entry.recursive:
        raise PeerAuthError(
            f"Recursive flag mismatch for '{remote_entry.id}':"
            f" peer={remote_entry.recursive}, local={local_entry.recursive}"
        )
