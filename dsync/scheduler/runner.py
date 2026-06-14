"""Background asyncio loop that runs folders on their cron schedule."""

from __future__ import annotations

import asyncio
import datetime
from typing import TYPE_CHECKING, Any

from dsync.config import SyncMode
from dsync.identity import PeerMapStore
from dsync.scheduler.cron import is_due
from dsync.scheduler.logging_setup import get_scheduler_logger
from dsync.scheduler.state_store import SchedulerStateStore
from dsync.state import AppState

if TYPE_CHECKING:
    from pathlib import Path

    from dsync.config import FolderEntry

_TICK_SECONDS = 30
_DISCOVER_TIMEOUT = 8


def _now() -> datetime.datetime:
    """Return the current timezone-aware local time."""
    return datetime.datetime.now(tz=datetime.UTC).astimezone()


class SchedulerRunner:
    """Polls folder configs and runs due folders via the manual sync path."""

    def __init__(self, config_dir: Path, cert: str, key: str) -> None:
        """Bind the runner to a config dir and the TLS material to sync with.

        Args:
            config_dir: Directory holding the dsync configuration.
            cert: Path to the TLS certificate.
            key: Path to the TLS private key.
        """
        self.config_dir = config_dir
        self.cert = cert
        self.key = key
        self.store = SchedulerStateStore(config_dir)
        self.log = get_scheduler_logger(config_dir)
        self._locks: dict[str, asyncio.Lock] = {}
        self._warned: set[str] = set()

    def _lock(self, folder_id: str) -> asyncio.Lock:
        """Return (creating if needed) the per-folder lock.

        Args:
            folder_id: The folder whose lock to fetch.

        Returns:
            The folder's ``asyncio.Lock``.
        """
        lock = self._locks.get(folder_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[folder_id] = lock
        return lock

    async def run(self) -> None:
        """Run the poll loop forever (one tick every ``_TICK_SECONDS``)."""
        self.log.info("scheduler started config_dir=%s", self.config_dir)
        while True:
            try:
                await self._tick()
            except Exception:
                self.log.exception("tick failed")
            await asyncio.sleep(_TICK_SECONDS)

    async def _tick(self) -> None:
        """Find due folders, discover peers once, then sync them in parallel."""
        # Deferred import: the scheduler reuses the manual sync path without
        # pulling the whole CLI at module load (avoids an import cycle).
        from dsync.cli.commands.sync.run_backup import _auto_discover_peers

        try:
            state = AppState.load(self.config_dir)
        except ValueError:
            self.log.exception("config invalid; skipping tick")
            return
        last_runs = self.store.load()
        now = _now()

        due: list[FolderEntry] = []
        for folder in state.folders.entries:
            if folder.interval is None:
                continue
            if folder.mode == SyncMode.BACKUP_FROM_PEER:
                if folder.id not in self._warned:
                    self.log.warning("folder=%s interval ignored (receive-only)", folder.id)
                    self._warned.add(folder.id)
                continue
            if not is_due(folder.interval, last_runs.get(folder.id), now):
                continue
            if self._lock(folder.id).locked():
                self.log.info("folder=%s still running; tick skipped", folder.id)
                continue
            due.append(folder)

        if not due:
            return

        peer_map = await asyncio.to_thread(
            _auto_discover_peers, PeerMapStore(), _DISCOVER_TIMEOUT, self.cert, self.key
        )
        await asyncio.gather(
            *(self._run_folder(folder, state, peer_map) for folder in due),
            return_exceptions=True,
        )

    async def _run_folder(
        self,
        folder: FolderEntry,
        state: AppState,
        peer_map: dict[str, Any],
    ) -> None:
        """Sync one folder via the manual code path, under its lock.

        On success the run time is recorded. On failure (or no peers) nothing is
        recorded, so the folder stays due and is retried on the next tick.

        Args:
            folder: The folder to sync.
            state: The resolved application state.
            peer_map: Discovered fingerprint-to-peer mapping for this tick.
        """
        from dsync.cli.commands.sync.run_backup import _sync_all_folders

        async with self._lock(folder.id):
            started = _now()
            self.log.info("folder=%s running", folder.id)
            try:
                total, failed = await _sync_all_folders(
                    [folder], peer_map, None, self.cert, self.key, state
                )
            except Exception:
                self.log.exception("folder=%s result=failure", folder.id)
                return
            duration = (_now() - started).total_seconds()
            if failed == 0 and total > 0:
                self.store.record(folder.id, started)
                self.log.info(
                    "folder=%s result=success total=%d failed=%d duration=%.1fs",
                    folder.id,
                    total,
                    failed,
                    duration,
                )
            else:
                self.log.warning(
                    "folder=%s result=failure total=%d failed=%d duration=%.1fs",
                    folder.id,
                    total,
                    failed,
                    duration,
                )
