from pathlib import Path

from pydantic import ValidationError
import pytest

from dsync.config import FolderEntry, FoldersConfig, SyncMode


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    config = FoldersConfig.load(tmp_path)
    assert config.entries == []


def test_load_valid_yaml(tmp_path: Path) -> None:
    (tmp_path / FoldersConfig.FILENAME).write_text(
        "entries:\n  - id: f1\n    path: /some/path\n    mode: mirror\n"
    )
    config = FoldersConfig.load(tmp_path)
    assert len(config.entries) == 1
    assert config.entries[0].id == "f1"
    assert config.entries[0].path == Path("/some/path")


def test_duplicate_id_raises() -> None:
    with pytest.raises(ValidationError, match="duplicate id"):
        FoldersConfig(
            entries=[
                FolderEntry(id="same", path=Path("/a"), mode=SyncMode.MIRROR),
                FolderEntry(id="same", path=Path("/b"), mode=SyncMode.MIRROR),
            ]
        )


def test_sync_mode_mirror() -> None:
    entry = FolderEntry(id="f", path=Path("/p"), mode=SyncMode.MIRROR)
    assert entry.mode == SyncMode.MIRROR
    assert entry.mode.value == "mirror"


def test_sync_mode_backup_to_peer() -> None:
    entry = FolderEntry(id="f", path=Path("/p"), mode=SyncMode.BACKUP_TO_PEER)
    assert entry.mode == SyncMode.BACKUP_TO_PEER
    assert entry.mode.value == "backup-to-peer"


def test_sync_mode_backup_from_peer() -> None:
    entry = FolderEntry(id="f", path=Path("/p"), mode=SyncMode.BACKUP_FROM_PEER)
    assert entry.mode == SyncMode.BACKUP_FROM_PEER
    assert entry.mode.value == "backup-from-peer"


def test_invalid_sync_mode_raises() -> None:
    with pytest.raises(ValidationError):
        FolderEntry(id="f", path=Path("/p"), mode="invalid-mode")  # type: ignore[arg-type]


def test_devices_none_when_omitted() -> None:
    entry = FolderEntry(id="f", path=Path("/p"), mode=SyncMode.MIRROR)
    assert entry.devices is None


def test_devices_explicit_list_preserved() -> None:
    entry = FolderEntry(id="f", path=Path("/p"), mode=SyncMode.MIRROR, devices=["dev-a", "dev-b"])
    assert entry.devices == ["dev-a", "dev-b"]


def test_recursive_defaults_to_true() -> None:
    entry = FolderEntry(id="f", path=Path("/p"), mode=SyncMode.MIRROR)
    assert entry.recursive is True


def test_recursive_can_be_false() -> None:
    entry = FolderEntry(id="f", path=Path("/p"), mode=SyncMode.MIRROR, recursive=False)
    assert entry.recursive is False


def test_round_trip_yaml(tmp_path: Path) -> None:
    original = FoldersConfig(
        entries=[
            FolderEntry(id="f1", path=Path("/data/a"), mode=SyncMode.MIRROR),
            FolderEntry(
                id="f2",
                path=Path("/data/b"),
                mode=SyncMode.BACKUP_TO_PEER,
                devices=["dev-x"],
                recursive=False,
            ),
        ]
    )
    original.save(tmp_path)
    loaded = FoldersConfig.load(tmp_path)
    assert loaded.entries[0].id == "f1"
    assert loaded.entries[0].mode == SyncMode.MIRROR
    assert loaded.entries[1].devices == ["dev-x"]
    assert loaded.entries[1].recursive is False


def test_mode_parsed_from_string_in_yaml(tmp_path: Path) -> None:
    (tmp_path / FoldersConfig.FILENAME).write_text(
        "entries:\n  - id: f1\n    path: /p\n    mode: backup-from-peer\n"
    )
    config = FoldersConfig.load(tmp_path)
    assert config.entries[0].mode == SyncMode.BACKUP_FROM_PEER
