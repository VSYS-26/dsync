"""Tests for the receiver-side `apply_renames` filesystem operation."""

from pathlib import Path

import pytest

from dsync.network.errors import TransferIntegrityError
from dsync.network.file_transfer import apply_renames
from dsync.network.index_diff import RenamePair


async def test_apply_basic_rename(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")

    applied = await apply_renames(
        [RenamePair(old_path="a.txt", new_path="b.txt", sha256="x")], tmp_path
    )

    assert applied == 1
    assert not (tmp_path / "a.txt").exists()
    assert (tmp_path / "b.txt").read_text() == "hello"


async def test_apply_rename_creates_parent_dirs(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("data")

    applied = await apply_renames(
        [RenamePair(old_path="a.txt", new_path="sub/dir/b.txt", sha256="x")], tmp_path
    )

    assert applied == 1
    assert (tmp_path / "sub" / "dir" / "b.txt").read_text() == "data"


async def test_missing_source_is_skipped_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "present.txt").write_text("ok")

    applied = await apply_renames(
        [
            RenamePair(old_path="absent.txt", new_path="x.txt", sha256="x"),
            RenamePair(old_path="present.txt", new_path="y.txt", sha256="x"),
        ],
        tmp_path,
    )

    assert applied == 1  # only the present one moved
    assert (tmp_path / "y.txt").exists()
    assert not (tmp_path / "x.txt").exists()


@pytest.mark.parametrize(
    ("old_path", "new_path"),
    [
        ("../escape.txt", "b.txt"),
        ("a.txt", "../escape.txt"),
        ("a.txt", "/etc/passwd"),
        ("sub/../../escape.txt", "b.txt"),
    ],
)
async def test_path_traversal_is_rejected(tmp_path: Path, old_path: str, new_path: str) -> None:
    (tmp_path / "a.txt").write_text("x")

    with pytest.raises(TransferIntegrityError):
        await apply_renames(
            [RenamePair(old_path=old_path, new_path=new_path, sha256="x")], tmp_path
        )


async def test_empty_rename_list_is_noop(tmp_path: Path) -> None:
    assert await apply_renames([], tmp_path) == 0
