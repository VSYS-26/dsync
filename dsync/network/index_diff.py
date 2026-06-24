"""Compare two `FolderIndex` snapshots to decide what needs transferring."""

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum

import yaml

from dsync.network.index_exchange import FileIndexEntry, FolderIndex


class Role(Enum):
    """Which side of the sync session this node is acting as.

    Determines which side's "only present here" entries become uploaded
    vs. downloaded when classifying a diff.
    """

    SOURCE = "source"
    """This node sends payload (client / sender)."""
    RECEIVER = "receiver"
    """This node receives payload (server / receiver)."""


@dataclass(frozen=True)
class RenamePair:
    """A single file that moved/was renamed without its content changing.

    Identifies a file (by `sha256`) that exists on both sides under two
    different relative paths. The receiver can satisfy this by moving its
    own copy instead of downloading the bytes again.

    Attributes:
        old_path: The file's current path on the *receiver's* disk - the
            one that should be moved away from.
        new_path: The path the file should end up at (the source's path).
        sha256: Hex SHA-256 the matched file has on both sides.
    """

    old_path: str
    new_path: str
    sha256: str


@dataclass(frozen=True)
class IndexDiff:
    """Result of comparing a local `FolderIndex` against a peer's.

    Attributes:
        to_upload: Entries this node needs to send to the peer.
        to_download: Entries this node needs to receive from the peer.
        unchanged: Paths present on both sides with an identical SHA-256.
            Must be excluded from any subsequent tranfer.
        renamed: Files present on both sides with identical content but a
            different path - to be handled by a move/rename command rather
            than re-transferring the bytes. Entries that became part of a
            rename pair are removed from `to_upload`/`to_download`.
    """

    to_upload: list[FileIndexEntry]
    to_download: list[FileIndexEntry]
    unchanged: list[str]
    renamed: list[RenamePair]


def diff_indexes(local: FolderIndex, peer: FolderIndex, role: Role) -> IndexDiff:
    """Compare `local` and `peer` and classify every path.

    For each relative path:
        - Present on both sides with the same SHA-256: `unchanged`.
        - Present only locally, or present on both with differing SHA-256
            and this node is the `Role.SOURCE` -> `to_upload`.
        - Present only on the peer, or present on both with differing SHA-256
            and this node is the `Role.RECEIVER` -> `to_download`.

    A path with diverging content on both sides is therefore assigned to
    exactly one of `to_upload` / `to_download` depending on `role`, not
    both - the session has a single sender and a single receiver, so only
    one direction is actionable in the current single-zip transfer model.

    Files that exist on just one side are not classified immediately:
    after the per-path pass, single-side-only entries are matched by
    SHA-256 across the two sides to detect renames/moves (see
    `_detect_renames`). A matched pair goes into `renamed` and is *not*
    added to `to_upload`/`to_download`, so a pure rename produces no
    payload transfer at all.

    Args:
        local: This node's own, freshly built `FolderIndex`.
        peer: The peer's `FolderIndex`, received via `IndexExchange`.
        role: Whether this node is the source (sender) or receiver for
            this sync session.

    Returns:
        An `IndexDiff` with `to_upload`, `to_download` and `unchanged`
        populated according to `role`. `local.folder_id` and `peer.folder_id`
        are not checked here -
        `IndexExchange.exchange` already validates that before this function is ever called.
    """
    local_by_path = local.as_dict()
    peer_by_path = peer.as_dict()

    to_upload: list[FileIndexEntry] = []
    to_download: list[FileIndexEntry] = []
    unchanged: list[str] = []
    local_only: list[FileIndexEntry] = []
    peer_only: list[FileIndexEntry] = []

    all_paths = sorted(local_by_path.keys() | peer_by_path.keys())

    for path in all_paths:
        local_entry = local_by_path.get(path)
        peer_entry = peer_by_path.get(path)

        if local_entry is not None and peer_entry is not None:
            if local_entry.sha256 == peer_entry.sha256:
                unchanged.append(path)
            elif role is Role.SOURCE:
                to_upload.append(local_entry)
            else:
                to_download.append(peer_entry)
        elif local_entry is not None:
            local_only.append(local_entry)
        else:
            # peer_entry is guaranteed non-None here since path came from
            # local_by_path | peer_by_path and local_entry is None.
            assert peer_entry is not None
            peer_only.append(peer_entry)

    renamed, matched_local, matched_peer = _detect_renames(local_only, peer_only, role)

    # Single-side entries that did not become part of a rename pair fall
    # back to a plain add/delete and are assigned by role: a source uploads
    # its extra files, a receiver downloads the peer's extra files. The
    # opposite side has nothing actionable for them in this transfer model.
    if role is Role.SOURCE:
        to_upload.extend(e for e in local_only if e.path not in matched_local)
    else:
        to_download.extend(e for e in peer_only if e.path not in matched_peer)

    return IndexDiff(
        to_upload=to_upload,
        to_download=to_download,
        unchanged=unchanged,
        renamed=renamed,
    )


def _detect_renames(
    local_only: list[FileIndexEntry],
    peer_only: list[FileIndexEntry],
    role: Role,
) -> tuple[list[RenamePair], set[str], set[str]]:
    """Match single-side-only entries by SHA-256 to find renames/moves.

    A file is treated as renamed when its content (SHA-256) is present on
    exactly one local-only path *and* exactly one peer-only path. The
    one-to-one requirement is deliberate: if the same hash appears under
    several unmatched paths on either side (e.g. duplicate files, or
    multiple empty files which all share the SHA-256 of empty content),
    the pairing is ambiguous, so those entries are left as ordinary
    add/delete rather than guessing a rename.

    The `old_path`/`new_path` orientation is normalized so it is identical
    no matter which side computes the diff: `old_path` is always the path
    on the receiver's disk (the one to move away from) and `new_path` the
    source's path (the target).

    Args:
        local_only: Entries present only in the local index.
        peer_only: Entries present only in the peer index.
        role: Whether this node is the source or the receiver.

    Returns:
        A tuple of (rename pairs sorted by `new_path`, set of matched
        local paths, set of matched peer paths). The path sets let the
        caller exclude matched entries from `to_upload`/`to_download`.
    """
    local_by_hash: dict[str, list[FileIndexEntry]] = defaultdict(list)
    for entry in local_only:
        local_by_hash[entry.sha256].append(entry)
    peer_by_hash: dict[str, list[FileIndexEntry]] = defaultdict(list)
    for entry in peer_only:
        peer_by_hash[entry.sha256].append(entry)

    renamed: list[RenamePair] = []
    matched_local: set[str] = set()
    matched_peer: set[str] = set()

    for digest, locals_ in local_by_hash.items():
        peers_ = peer_by_hash.get(digest)
        if peers_ is None or len(locals_) != 1 or len(peers_) != 1:
            continue  # absent on the other side, or ambiguous -> not a rename
        local_entry = locals_[0]
        peer_entry = peers_[0]
        if role is Role.SOURCE:
            # local is the source: its path is the target, the peer
            # (receiver) holds the file under its current/old path.
            renamed.append(
                RenamePair(old_path=peer_entry.path, new_path=local_entry.path, sha256=digest)
            )
        else:
            # local is the receiver: its own path is the one to move away
            # from, the peer (source) defines the target path.
            renamed.append(
                RenamePair(old_path=local_entry.path, new_path=peer_entry.path, sha256=digest)
            )
        matched_local.add(local_entry.path)
        matched_peer.add(peer_entry.path)

    renamed.sort(key=lambda r: r.new_path)
    return renamed, matched_local, matched_peer


def renames_to_yaml(renamed: list[RenamePair]) -> bytes:
    """Serialize rename pairs as a YAML-encoded byte payload for the wire."""
    return yaml.safe_dump(
        [{"old": r.old_path, "new": r.new_path, "sha256": r.sha256} for r in renamed]
    ).encode("utf-8")


def renames_from_yaml(data: bytes) -> list[RenamePair]:
    """Parse a YAML-encoded rename payload into a list of `RenamePair`.

    Raises:
        KeyError, TypeError, yaml.YAMLError: If the payload is malformed.
    """
    raw = yaml.safe_load(data.decode("utf-8")) or []
    return [
        RenamePair(old_path=item["old"], new_path=item["new"], sha256=item["sha256"])
        for item in raw
    ]
