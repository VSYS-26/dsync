import datetime
import json
from pathlib import Path

from dsync.scheduler.state_store import SchedulerStateStore


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    store = SchedulerStateStore(tmp_path)
    assert store.load() == {}


def test_record_and_load_roundtrip(tmp_path: Path) -> None:
    store = SchedulerStateStore(tmp_path)
    when = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)

    store.record("f1", when)
    loaded = store.load()

    assert loaded["f1"] == when


def test_record_preserves_other_folders(tmp_path: Path) -> None:
    store = SchedulerStateStore(tmp_path)
    when1 = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    when2 = datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC)

    store.record("f1", when1)
    store.record("f2", when2)
    loaded = store.load()

    assert loaded["f1"] == when1
    assert loaded["f2"] == when2


def test_load_corrupt_json_returns_empty(tmp_path: Path) -> None:
    store = SchedulerStateStore(tmp_path)
    store.file_path.write_text("not valid json{")

    assert store.load() == {}


def test_load_non_dict_json_returns_empty(tmp_path: Path) -> None:
    store = SchedulerStateStore(tmp_path)
    store.file_path.write_text(json.dumps(["a", "b"]))

    assert store.load() == {}


def test_load_skips_non_string_entries(tmp_path: Path) -> None:
    store = SchedulerStateStore(tmp_path)
    store.file_path.write_text(json.dumps({"f1": 123, "f2": "2026-01-01T00:00:00+00:00"}))

    loaded = store.load()

    assert "f1" not in loaded
    assert "f2" in loaded


def test_load_skips_invalid_datetime_strings(tmp_path: Path) -> None:
    store = SchedulerStateStore(tmp_path)
    store.file_path.write_text(json.dumps({"f1": "not-a-timestamp"}))

    assert store.load() == {}
