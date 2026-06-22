from pathlib import Path

import pytest

from dsync.config import FolderEntry, SyncMode
from dsync.network.config_validation import validate_peer_folder_config
from dsync.network.errors import PeerAuthError


def _entry(mode: SyncMode, devices: list[str] | None = None, recursive: bool = True) -> FolderEntry:
    return FolderEntry(
        id="folder-a", path=Path("/data"), mode=mode, devices=devices, recursive=recursive
    )


def test_mirror_to_mirror_valid() -> None:
    validate_peer_folder_config(_entry(SyncMode.MIRROR), _entry(SyncMode.MIRROR), "peer-1")


def test_backup_to_peer_from_peer_valid() -> None:
    validate_peer_folder_config(
        _entry(SyncMode.BACKUP_FROM_PEER),
        _entry(SyncMode.BACKUP_TO_PEER),
        "peer-1",
    )


def test_peer_sends_backup_from_peer_raises() -> None:
    with pytest.raises(PeerAuthError, match="backup-from-peer"):
        validate_peer_folder_config(
            _entry(SyncMode.BACKUP_TO_PEER),
            _entry(SyncMode.BACKUP_FROM_PEER),
            "peer-1",
        )


def test_mode_mismatch_mirror_vs_backup_raises() -> None:
    with pytest.raises(PeerAuthError, match="Mode mismatch"):
        validate_peer_folder_config(
            _entry(SyncMode.BACKUP_FROM_PEER),
            _entry(SyncMode.MIRROR),
            "peer-1",
        )


def test_mode_mismatch_backup_vs_mirror_raises() -> None:
    with pytest.raises(PeerAuthError, match="Mode mismatch"):
        validate_peer_folder_config(
            _entry(SyncMode.MIRROR),
            _entry(SyncMode.BACKUP_TO_PEER),
            "peer-1",
        )


def test_peer_not_in_device_whitelist_raises() -> None:
    local = _entry(SyncMode.BACKUP_FROM_PEER, devices=["allowed-peer"])
    remote = _entry(SyncMode.BACKUP_TO_PEER)
    with pytest.raises(PeerAuthError, match="not whitelisted"):
        validate_peer_folder_config(local, remote, "unknown-peer")


def test_peer_in_device_whitelist_passes() -> None:
    local = _entry(SyncMode.BACKUP_FROM_PEER, devices=["peer-1", "peer-2"])
    remote = _entry(SyncMode.BACKUP_TO_PEER)
    validate_peer_folder_config(local, remote, "peer-1")


def test_none_device_list_skips_whitelist_check() -> None:
    local = _entry(SyncMode.BACKUP_FROM_PEER, devices=None)
    remote = _entry(SyncMode.BACKUP_TO_PEER)
    validate_peer_folder_config(local, remote, "any-peer")


def test_recursive_mismatch_raises() -> None:
    local = _entry(SyncMode.MIRROR, recursive=True)
    remote = _entry(SyncMode.MIRROR, recursive=False)
    with pytest.raises(PeerAuthError, match="Recursive flag mismatch"):
        validate_peer_folder_config(local, remote, "peer-1")


def test_recursive_match_passes() -> None:
    local = _entry(SyncMode.MIRROR, recursive=False)
    remote = _entry(SyncMode.MIRROR, recursive=False)
    validate_peer_folder_config(local, remote, "peer-1")
