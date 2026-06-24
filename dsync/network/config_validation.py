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
    own_device_id: str,
) -> None:
    """Verify ``remote_entry`` is a legal counterpart to ``local_entry``.

    Error messages name both devices explicitly so the same text is correct
    no matter which side reads it (the receiver of an ERROR frame, or the
    side that raised). Never use "local"/"peer" — those flip meaning
    depending on which terminal is reading the log.

    Args:
        local_entry: This node's own folder configuration for the id.
        remote_entry: The sending peer's configuration for the same id.
        source_peer_id: Trusted-device id of the sender (the source of data).
        own_device_id: This node's own device id (the receiver of data).

    Raises:
        PeerAuthError: If the configurations contradict each other.
    """
    if remote_entry.mode == SyncMode.BACKUP_FROM_PEER:
        raise PeerAuthError(
            f"folder '{remote_entry.id}': device '{source_peer_id}' attempted to send "
            f"in backup-from-peer mode - invalid direction (only the receiving device "
            f"may use backup-from-peer)"
        )

    expected_local_mode = _COMPLEMENTARY_MODES.get(remote_entry.mode)
    if expected_local_mode is None:
        raise PeerAuthError(
            f"folder '{remote_entry.id}': device '{source_peer_id}' uses unsupported "
            f"mode '{remote_entry.mode.value}'"
        )
    if local_entry.mode != expected_local_mode:
        raise PeerAuthError(
            f"Mode mismatch for folder '{remote_entry.id}': "
            f"device '{source_peer_id}' configured '{remote_entry.mode.value}', "
            f"device '{own_device_id}' configured '{local_entry.mode.value}' "
            f"(expected device '{own_device_id}'='{expected_local_mode.value}')"
        )

    if local_entry.devices is not None and source_peer_id not in local_entry.devices:
        raise PeerAuthError(
            f"folder '{remote_entry.id}': device '{source_peer_id}' is not whitelisted "
            f"in device '{own_device_id}'s folder devices list"
        )

    if local_entry.recursive != remote_entry.recursive:
        raise PeerAuthError(
            f"Recursive flag mismatch for folder '{remote_entry.id}': "
            f"device '{source_peer_id}' configured recursive={remote_entry.recursive}, "
            f"device '{own_device_id}' configured recursive={local_entry.recursive}"
        )
