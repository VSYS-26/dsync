from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from dsync.config import DaemonConfig, DevicesConfig, FolderEntry, FoldersConfig, SyncMode
from dsync.scheduler.runner import SchedulerRunner
from dsync.state import AppState


def _runner(tmp_path: Path) -> SchedulerRunner:
    return SchedulerRunner(tmp_path, cert="cert.pem", key="key.pem")


def _state(*entries: FolderEntry) -> AppState:
    return AppState(
        config_dir=Path("/config"),
        folders=FoldersConfig(entries=list(entries)),
        devices=DevicesConfig(trusted_devices=[]),
        daemon=DaemonConfig(),
    )


_RUN_BACKUP = "dsync.cli.commands.sync.run_backup"


async def test_tick_skips_invalid_config(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    with (
        patch.object(AppState, "load", side_effect=ValueError("bad config")),
        patch(f"{_RUN_BACKUP}._auto_discover_peers") as mock_discover,
    ):
        await runner._tick()

    mock_discover.assert_not_called()


async def test_tick_no_interval_folders_is_noop(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    entry = FolderEntry(id="f1", path=Path("/data"), mode=SyncMode.MIRROR)
    with (
        patch.object(AppState, "load", return_value=_state(entry)),
        patch(f"{_RUN_BACKUP}._auto_discover_peers") as mock_discover,
    ):
        await runner._tick()

    mock_discover.assert_not_called()


async def test_tick_backup_from_peer_warns_once_and_skips(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    entry = FolderEntry(
        id="f1", path=Path("/data"), mode=SyncMode.BACKUP_FROM_PEER, interval="* * * * *"
    )
    with (
        patch.object(AppState, "load", return_value=_state(entry)),
        patch(f"{_RUN_BACKUP}._auto_discover_peers") as mock_discover,
    ):
        await runner._tick()
        await runner._tick()

    mock_discover.assert_not_called()
    assert runner._warned == {"f1"}


async def test_tick_not_due_folder_is_skipped(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    entry = FolderEntry(id="f1", path=Path("/data"), mode=SyncMode.MIRROR, interval="* * * * *")
    with (
        patch.object(AppState, "load", return_value=_state(entry)),
        patch("dsync.scheduler.runner.is_due", return_value=False),
        patch(f"{_RUN_BACKUP}._auto_discover_peers") as mock_discover,
    ):
        await runner._tick()

    mock_discover.assert_not_called()


async def test_tick_locked_folder_is_skipped(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    entry = FolderEntry(id="f1", path=Path("/data"), mode=SyncMode.MIRROR, interval="* * * * *")
    await runner._lock("f1").acquire()
    with (
        patch.object(AppState, "load", return_value=_state(entry)),
        patch("dsync.scheduler.runner.is_due", return_value=True),
        patch(f"{_RUN_BACKUP}._auto_discover_peers") as mock_discover,
    ):
        await runner._tick()

    mock_discover.assert_not_called()


async def test_tick_due_folder_discovers_and_syncs(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    entry = FolderEntry(id="f1", path=Path("/data"), mode=SyncMode.MIRROR, interval="* * * * *")
    with (
        patch.object(AppState, "load", return_value=_state(entry)),
        patch("dsync.scheduler.runner.is_due", return_value=True),
        patch(f"{_RUN_BACKUP}._auto_discover_peers", return_value={"fp": "peer"}) as mock_discover,
        patch(f"{_RUN_BACKUP}._sync_all_folders", new_callable=AsyncMock) as mock_sync,
    ):
        mock_sync.return_value = (1, 0)
        await runner._tick()

    mock_discover.assert_called_once()
    mock_sync.assert_called_once()


# ── _run_folder ───────────────────────────────────────────────────────────────


async def test_run_folder_success_records_run(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    entry = FolderEntry(id="f1", path=Path("/data"), mode=SyncMode.MIRROR)
    state = _state(entry)

    with patch(f"{_RUN_BACKUP}._sync_all_folders", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = (1, 0)
        await runner._run_folder(entry, state, {})

    recorded = runner.store.load()
    assert "f1" in recorded


async def test_run_folder_failure_does_not_record(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    entry = FolderEntry(id="f1", path=Path("/data"), mode=SyncMode.MIRROR)
    state = _state(entry)

    with patch(f"{_RUN_BACKUP}._sync_all_folders", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = (1, 1)
        await runner._run_folder(entry, state, {})

    assert runner.store.load() == {}


async def test_run_folder_no_peers_synced_does_not_record(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    entry = FolderEntry(id="f1", path=Path("/data"), mode=SyncMode.MIRROR)
    state = _state(entry)

    with patch(f"{_RUN_BACKUP}._sync_all_folders", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = (0, 0)
        await runner._run_folder(entry, state, {})

    assert runner.store.load() == {}


async def test_run_folder_exception_is_caught(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    entry = FolderEntry(id="f1", path=Path("/data"), mode=SyncMode.MIRROR)
    state = _state(entry)

    with patch(f"{_RUN_BACKUP}._sync_all_folders", new_callable=AsyncMock) as mock_sync:
        mock_sync.side_effect = RuntimeError("boom")
        await runner._run_folder(entry, state, {})

    assert runner.store.load() == {}


# ── run loop ──────────────────────────────────────────────────────────────────


class _StopLoopError(Exception):
    pass


async def test_run_loop_calls_tick_and_sleeps(tmp_path: Path) -> None:
    runner = _runner(tmp_path)

    with (
        patch.object(runner, "_tick", new_callable=AsyncMock) as mock_tick,
        patch("dsync.scheduler.runner.asyncio.sleep", side_effect=_StopLoopError) as mock_sleep,
        pytest.raises(_StopLoopError),
    ):
        await runner.run()

    mock_tick.assert_called_once()
    mock_sleep.assert_called_once()


async def test_run_loop_continues_after_tick_exception(tmp_path: Path) -> None:
    runner = _runner(tmp_path)

    with (
        patch.object(runner, "_tick", new_callable=AsyncMock, side_effect=RuntimeError("boom")),
        patch("dsync.scheduler.runner.asyncio.sleep", side_effect=_StopLoopError),
        pytest.raises(_StopLoopError),
    ):
        await runner.run()
