"""Persistence of per-folder last-successful-run timestamps."""

from __future__ import annotations

import datetime
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

FILENAME = "scheduler-state.json"


class SchedulerStateStore:
    """Stores the last successful run time per folder as JSON.

    Kept separate from the YAML configs on purpose: this is runtime state, not
    user configuration.
    """

    def __init__(self, config_dir: Path) -> None:
        """Bind the store to ``config_dir/scheduler-state.json``.

        Args:
            config_dir: Directory holding the dsync configuration.
        """
        self.file_path = config_dir / FILENAME

    def load(self) -> dict[str, datetime.datetime]:
        """Return the folder-id to last-run-timestamp mapping.

        Returns:
            Mapping of folder id to a timezone-aware datetime. Empty if the file
            is missing or unreadable.
        """
        if not self.file_path.is_file():
            return {}
        try:
            raw = json.loads(self.file_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        result: dict[str, datetime.datetime] = {}
        for folder_id, value in raw.items():
            if not isinstance(folder_id, str) or not isinstance(value, str):
                continue
            try:
                result[folder_id] = datetime.datetime.fromisoformat(value)
            except ValueError:
                continue
        return result

    def record(self, folder_id: str, when: datetime.datetime) -> None:
        """Persist ``when`` as the last successful run time of ``folder_id``.

        Args:
            folder_id: The folder whose run time to record.
            when: The (timezone-aware) run timestamp.
        """
        current = self.load()
        current[folder_id] = when
        payload = {fid: ts.isoformat() for fid, ts in current.items()}
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
