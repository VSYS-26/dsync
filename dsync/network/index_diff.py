"""Compare two `FolderIndex` snapshots to decide what needs transferring."""

from dataclasses import dataclass
from enum import Enum

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
class IndexDiff:
    """Result of comparing a local `FolderIndex` against a peer's.
    
    Attributes:
        to_upload: Entries this node needs to send to the peer.
        to_download: Entries this node needs to receive from the peer.
        unchanged: Paths present on both sides with an identical SHA-256.
            Must be excluded from any subsequent tranfer.
    """

    to_upload: list[FileIndexEntry]
    to_download: list[FileIndexEntry]
    unchanged: list[str]


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
    
    Renamed-without-content-change detection is intentionally out of
    scope here; a path-only match is treated as an unrelated add/delete
    pair like any other path that exists on just one side.
    
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

    to_upload = list[FileIndexEntry] = []
    to_download = list[FileIndexEntry] = []
    unchanged = list[str] = []

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
            # Only on this node's side
            if role is Role.SOURCE:
                to_upload.append(local_entry)
            # else: receiver-only local file with no peer counterpart is
            # not something this transfer model can act on (nothing to
            # download for it, and a receiver does not currently upload).
            else:
                # Only on the peer's side. peer_entry is guaranteed non-None
                # here since path came from local_py_path | peer_by_path and
                # local_entry is None.
                assert peer_entry is not None
                if role is Role.RECEIVER:
                    to_download.append(peer_entry)
                # else: source-side has nothing to do with a peer-only file
                # in this transfer model (a source does not currently 
                # request downloads).

    return IndexDiff(to_upload=to_upload, to_download=to_download, unchanged=unchanged)
    