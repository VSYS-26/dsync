"""Unit tests for rename/move detection in `diff_indexes`."""

from dsync.network.index_diff import (
    RenamePair,
    Role,
    diff_indexes,
    renames_from_yaml,
    renames_to_yaml,
)
from dsync.network.index_exchange import FileIndexEntry, FolderIndex

H = "a" * 64  # stand-in SHA-256 of "the renamed file"
H2 = "b" * 64
N = "c" * 64


def _index(*entries: tuple[str, str]) -> FolderIndex:
    """Build a FolderIndex from (path, sha256) pairs; size is irrelevant here."""
    files = [FileIndexEntry(path=p, size=1, sha256=s) for p, s in entries]
    return FolderIndex(folder_id="f", generated_at=0.0, files=files)


def test_pure_rename_source_detected_and_not_uploaded() -> None:
    local = _index(("b.txt", H))  # source already has the new name
    peer = _index(("a.txt", H))  # receiver still has the old name

    diff = diff_indexes(local, peer, role=Role.SOURCE)

    assert diff.renamed == [RenamePair(old_path="a.txt", new_path="b.txt", sha256=H)]
    assert diff.to_upload == []  # nothing to send: pure rename
    assert diff.to_download == []


def test_pure_rename_receiver_same_orientation() -> None:
    # Same scenario from the receiver's point of view: old/new must match
    # the source's view so the command is applied identically either way.
    local = _index(("a.txt", H))  # receiver's current (old) name
    peer = _index(("b.txt", H))  # source's (new) name

    diff = diff_indexes(local, peer, role=Role.RECEIVER)

    assert diff.renamed == [RenamePair(old_path="a.txt", new_path="b.txt", sha256=H)]
    assert diff.to_download == []
    assert diff.to_upload == []


def test_rename_into_subdirectory() -> None:
    local = _index(("sub/dir/b.txt", H))
    peer = _index(("a.txt", H))

    diff = diff_indexes(local, peer, role=Role.SOURCE)

    assert diff.renamed == [RenamePair(old_path="a.txt", new_path="sub/dir/b.txt", sha256=H)]
    assert diff.to_upload == []


def test_rename_plus_unchanged_file() -> None:
    local = _index(("b.txt", H), ("keep.txt", N))
    peer = _index(("a.txt", H), ("keep.txt", N))

    diff = diff_indexes(local, peer, role=Role.SOURCE)

    assert diff.renamed == [RenamePair(old_path="a.txt", new_path="b.txt", sha256=H)]
    assert diff.unchanged == ["keep.txt"]
    assert diff.to_upload == []


def test_rename_plus_genuine_new_file_still_uploads_the_new_one() -> None:
    local = _index(("b.txt", H), ("new.txt", N))  # rename + a brand new file
    peer = _index(("a.txt", H))

    diff = diff_indexes(local, peer, role=Role.SOURCE)

    assert diff.renamed == [RenamePair(old_path="a.txt", new_path="b.txt", sha256=H)]
    assert [e.path for e in diff.to_upload] == ["new.txt"]


def test_duplicate_hash_on_one_side_is_not_a_rename() -> None:
    # Same content under two unmatched local paths -> ambiguous -> no rename.
    local = _index(("x.txt", H), ("y.txt", H))
    peer = _index(("a.txt", H))

    diff = diff_indexes(local, peer, role=Role.SOURCE)

    assert diff.renamed == []
    assert sorted(e.path for e in diff.to_upload) == ["x.txt", "y.txt"]


def test_different_hashes_do_not_match() -> None:
    local = _index(("b.txt", H))
    peer = _index(("a.txt", H2))

    diff = diff_indexes(local, peer, role=Role.SOURCE)

    assert diff.renamed == []
    assert [e.path for e in diff.to_upload] == ["b.txt"]


def test_modified_same_path_is_not_a_rename() -> None:
    # Same path, different content -> a content change, handled by role, never a rename.
    local = _index(("f.txt", H))
    peer = _index(("f.txt", H2))

    diff = diff_indexes(local, peer, role=Role.SOURCE)

    assert diff.renamed == []
    assert [e.path for e in diff.to_upload] == ["f.txt"]


def test_multiple_renames_sorted_by_new_path() -> None:
    local = _index(("z_new.txt", H), ("a_new.txt", N))
    peer = _index(("z_old.txt", H), ("a_old.txt", N))

    diff = diff_indexes(local, peer, role=Role.SOURCE)

    assert diff.renamed == [
        RenamePair(old_path="a_old.txt", new_path="a_new.txt", sha256=N),
        RenamePair(old_path="z_old.txt", new_path="z_new.txt", sha256=H),
    ]
    assert diff.to_upload == []


def test_renames_yaml_roundtrip() -> None:
    pairs = [
        RenamePair(old_path="a.txt", new_path="sub/b.txt", sha256=H),
        RenamePair(old_path="old/x", new_path="new/x", sha256=N),
    ]
    assert renames_from_yaml(renames_to_yaml(pairs)) == pairs


def test_renames_yaml_empty_roundtrip() -> None:
    assert renames_from_yaml(renames_to_yaml([])) == []
