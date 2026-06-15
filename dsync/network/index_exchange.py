"""Folder index generation and pre-transfer index exchange.

Before any file payload is transferred, both peers build a `FolderIndex` -
a manifest of every file in the folder being synced, identified by its
relative path, size and SHA-256 digest - and send it to the peer. This
gives both sides a complete picture of "what does the other side have
right now?" *before* anything about payload transfer is decided.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
import time

import yaml

from dsync.integrity import compute_sha256
from dsync.network.errors import FrameValidationError
from dsync.network.p2p_core import async_recv_index, async_send_index

@dataclass(frozen=True)
class FileIndexEntry:
    """One file's state as recorded in a `FolderIndex`.
    
    Attributes:
        path: POSIX-style path relative to the folder root.
        size: Size of the file in bytes.
        sha256: Hex SHA-256 digest of the file's full content.
    """

    path: str
    size: int
    sha256: str

@dataclass(frozen=True)
class FolderIndex:
    """Snapshot of a folder's contents, used to decide what needs syncing.

    Attributes:
        folder_id: ID of the `FolderEntry` this index describes.
        generated_at: Unix timestamp (seconds) when the index was built.
        files: One entry per regular file found under the indexed path,
            sorted by `path` for deterministic comparison and serialization.
    """

    folder_id: str
    generated_at: float
    files: list[FileIndexEntry]

    @classmethod
    async def build(cls, folder_id: str, path: Path, recursive: bool) -> "FolderIndex":
        """Walk `path` and hash every regular file it contains.
        
        Args:
            folder_id: ID of the `FolderEntry` this index describes.
            path: File or directory to index. If it doesn't exist yet
                (e.g. a sync target that has never been received), an
                empty index is returned.
            recursive: Whether to descend into subdirectories when `path`
                is a directory. Ignored if `path` is a file.
                
        Returns:
            A `FolderIndex` with one entry per file, sorted by path.
        """
        entries: list[FileIndexEntry] = []

        if path.is_file():
            digest = await asyncio.to_thread(compute_sha256, path)
            entries.append(
                FileIndexEntry(
                path=path.name,
                size=path.stat().st_size,
                sha256=digest,
                )
            )
        elif path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            for p in iterator:
                if not p.is_file():
                    continue
                digest = await asyncio.to_thread(compute_sha256, p)
                entries.append(
                    FileIndexEntry(
                    path=p.relative_to(path).as_posix(),
                    size=p.stat().st_size,
                    sha256=digest,
                    )
                )
        
        # else: path does not exist yet -> empty index, everything the
        # peer has for this folder counts as "new" from our side.

        entries.sort(key=lambda e: e.path)
        return cls(folder_id=folder_id, generated_at=time.time(), files=entries)
    

    def to_yaml(self) -> bytes:
        """Serialize this index as a YAML-encoded byte payload."""
        return yaml.safe_dump(
            {
                "folder_id": self.folder_id,
                "generated_at": self.generated_at,
                "files": [
                    {"path": e.path, "size": e.size, "sha256": e.sha256}
                    for e in self.files
                ],
            }
        ).encode("utf-8")


    @classmethod
    def from_yaml(cls, data: bytes) -> "FolderIndex":
        """Parse a YAML-encoded index payload into a `FolderIndex`."""
        raw = yaml.safe_load(data.decode("utf-8")) or {}
        files = [
            FileIndexEntry(
                path=f["path"],
                size=f["size"],
                sha256=f["sha256"]
            )
            for f in raw["files"]
        ]
        return cls(
            folder_id=raw["folder_id"],
            generated_at=raw["generated_at"],
            files=files
        )
    

    def as_dict(self) -> dict[str, FileIndexEntry]:
        """Return entries keyed by relative path, for O(1) diffing lookups."""
        return {e.path: e for e in self.files}
    

class IndexExchange:
    """Exchanges `FolderIndex` snaphots between peers before payload transfer.
    
    Both sides build their own index of the folder being synced and send
    it to the peer, and both sides receive the peer's index in return.
    The resulting pair (`own_index`, peer's `FolderIndex`) is the input
    for the upcoming diff step that decides which files actually need to move.
    
    The index is exchanged as a single YAML document over its own
    `MsgType.INDEX` frame (`async_send_index` / `async_recv_index`). Unlike
    the folder config, an index grows with the number of files in the
    folder, so it has its own, much larger size limit (`MAX_INDEX_SIZE`)
    rather than sharing the tight `MAX_CONFIG_SIZE` cap.
    """

    def __init__(self, own_index: FolderIndex) -> None:
        """Initialize with this node's own, already-built index."""
        self.own_index = own_index
    

    async def exchange(
        self,
        writer: asyncio.StreamWriter,
        reader: asyncio.StreamReader,
        is_source: bool,
    ) -> FolderIndex:
        """Send `own_index` and receive the peer's index.
        
        Args:
            writer: Stream writer to peer.
            reader: Stream reader from peer.
            is_source: Send-first/receive-first ordering flag, using the
                same convention as `ConfigExchange.exchange_and_validate`
                so both peers agree on who goes first.
        
        Returns:
            The peer's `FolderIndex`.
            
        Raises:
            FrameValidationError: If the peer's index is malformed, or
                desribes a different folder than `own_index`.
        """
        if is_source:
            await self._send_own_index(writer)
            peer_index = await self._recv_peer_index(reader)
        else:
            peer_index = await self._recv_peer_index(reader)
            await self._send_own_index(writer)
        
        if peer_index.folder_id != self.own_index.folder_id:
            raise FrameValidationError(
                f"Peer index folder_id mismatch: expected '{self.own_index.folder_id}', "
                f"got '{peer_index.folder_id}'"
            )
        
        print(
            f"[+] Index exchange completed: {len(self.own_index.files)} files locally, "
            f"/ {len(peer_index.files)} remote entries"
        )
        return peer_index
    

    async def _send_own_index(self, writer: asyncio.StreamWriter) -> None:
        payload = self.own_index.to_yaml()
        print(
            f"[*] Sending index for folder '{self.own_index.folder_id}' with "
            f"{len(self.own_index.files)} files ({len(payload)} bytes)..."
        )
        await async_send_index(writer, payload)


    async def _recv_peer_index(self, reader: asyncio.StreamReader) -> FolderIndex:
        print("[*] Waiting to receive peer's index...")
        payload = await async_recv_index(reader)
        print(f"[*] Received peer index ({len(payload)} bytes), parsing...")
        try:
            return FolderIndex.from_yaml(payload)
        except (KeyError, TypeError, yaml.YAMLError) as e:
            raise FrameValidationError(f"Failed to parse peer index: {e}") from e
