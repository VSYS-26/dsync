"""Async file transfer over an authenticated asyncio TLS stream."""

import asyncio
from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path, PurePosixPath

import yaml

from dsync.integrity import compute_sha256
from dsync.network.errors import (
    ChunkValidationError,
    FrameValidationError,
    TransferIntegrityError,
)
from dsync.network.p2p_core import MsgType, async_recv_msg, async_send_msg

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 64 * 1024
MAX_META_SIZE = 8 * 1024
MAX_CHUNK_SIZE = DEFAULT_CHUNK_SIZE


@dataclass(frozen=True)
class FileMeta:
    """Metadata announced by the sender before chunk frames arrive.

    Attributes:
        path: POSIX-style path of the file *relative to the configured
            folder root*. The receiver re-anchors this under its own
            folder root, preserving subdirectory structure.
        size: Total file size in bytes. Used as the stop condition for the
            receiver's chunk loop.
        sha256: Hex SHA-256 digest of the full file. Verified by the
            receiver after all chunks have been written.
    """

    path: str
    size: int
    sha256: str

    @classmethod
    def from_yaml(cls, data: bytes) -> "FileMeta":
        """Parse a YAML-encoded meta payload into a ``FileMeta`` instance."""
        raw = yaml.safe_load(data.decode("utf-8"))
        return cls(path=raw["path"], size=raw["size"], sha256=raw["sha256"])

    def to_yaml(self) -> bytes:
        """Serialize this metadata as a YAML-encoded byte payload."""
        return yaml.dump(
            {"path": self.path, "size": self.size, "sha256": self.sha256},
        ).encode("utf-8")


def _safe_resolve_under(dest_root: Path, rel_posix: str) -> Path:
    """Resolve ``rel_posix`` under ``dest_root``, rejecting traversal.

    Rejects absolute paths, empty components, and ``..`` segments before
    joining. After joining, re-checks that the resolved target stays
    inside the resolved ``dest_root`` — defends against symlinks placed
    by a prior run pointing outside the folder.
    """
    rel = PurePosixPath(rel_posix)
    if not rel_posix or rel.is_absolute() or rel.anchor:
        msg = "Refusing absolute or anchored path in file meta"
        raise TransferIntegrityError(msg)
    parts = [p for p in rel.parts if p != "."]
    if not parts or any(p == ".." for p in parts):
        msg = "Refusing path-traversal segment in file meta"
        raise TransferIntegrityError(msg)
    target = dest_root.joinpath(*parts)
    resolved_root = dest_root.resolve()
    parent_resolved = target.parent.resolve() if target.parent.exists() else None
    if parent_resolved is not None and not parent_resolved.is_relative_to(resolved_root):
        msg = "File meta resolves outside destination folder"
        raise TransferIntegrityError(msg)
    return target


async def _recv_frame(reader: asyncio.StreamReader) -> tuple[MsgType, bytes]:
    """Read one length-prefixed frame and validate its type and size.

    Re-raises :class:`asyncio.IncompleteReadError` on a clean EOF at the
    frame boundary so callers can detect a graceful end-of-stream.
    """
    raw_type, payload = await async_recv_msg(reader)
    if raw_type is None or payload is None:
        raise asyncio.IncompleteReadError(b"", 5)
    try:
        msg_type = MsgType(raw_type)
    except ValueError as exc:
        msg = "Unknown frame type"
        raise FrameValidationError(msg) from exc

    if msg_type == MsgType.FILE_META:
        max_len = MAX_META_SIZE
    elif msg_type == MsgType.FILE_CHUNK:
        max_len = MAX_CHUNK_SIZE
    else:
        msg = "Unsupported frame type"
        raise FrameValidationError(msg)

    if len(payload) > max_len:
        msg = "Frame payload exceeds maximum allowed size"
        raise FrameValidationError(msg)
    return msg_type, payload


async def send_file(
    writer: asyncio.StreamWriter, reader: asyncio.StreamReader, path: Path, root: Path
) -> None:
    """Send one file over an open asyncio TLS stream in chunks.

    Sends a YAML meta frame (path, size, sha256) followed by raw chunk
    frames of ``DEFAULT_CHUNK_SIZE`` bytes until the file is exhausted.
    The meta carries the path of ``path`` relative to ``root`` so the
    receiver can reproduce the source's directory layout.

    Args:
        writer: Authenticated asyncio stream from the connection setup.
        reader: Authenticated asyncio stream from the connection setup.
        path: Source file to transmit.
        root: Folder root the path should be relativized against.

    Raises:
        FileNotFoundError: If ``path`` does not exist or is not a regular file.
        ValueError: If ``path`` is not located under ``root``.
        TransferIntegrityError: If peer's hash verification fails.
    """
    if not path.is_file():
        msg = f"Not a regular file: {path}"
        raise FileNotFoundError(msg)
    rel = path.resolve().relative_to(root.resolve())
    digest = await asyncio.to_thread(compute_sha256, path)
    meta = FileMeta(path=rel.as_posix(), size=path.stat().st_size, sha256=digest)
    await async_send_msg(writer, MsgType.FILE_META, meta.to_yaml())

    with path.open("rb") as f:
        while chunk := await asyncio.to_thread(f.read, DEFAULT_CHUNK_SIZE):
            await async_send_msg(writer, MsgType.FILE_CHUNK, chunk)

    msg_type, peer_hash_bytes = await async_recv_msg(reader)
    if msg_type is None or peer_hash_bytes is None:
        msg = "Connection closed before receiving peer hash verification"
        raise TransferIntegrityError(msg)
    if msg_type != MsgType.FILE_VERIFY:
        msg = f"Expected FILE_VERIFY (type {MsgType.FILE_VERIFY}), got type {msg_type}"
        raise TransferIntegrityError(msg)

    peer_hash = peer_hash_bytes.decode("utf-8")
    if peer_hash != digest:
        raise TransferIntegrityError(
            f"Hash mismatch: source={digest}, peer={peer_hash}"
        )
    logger.info(f"[+] Verified: {path.name} (hash match confirmed by peer)")


async def _recv_and_verify_chunks(
    reader: asyncio.StreamReader, target: Path, meta: FileMeta
) -> str:
    """Read chunk frames into ``target`` and verify against ``meta.sha256``.

    Args:
        reader: Authenticated asyncio stream from the connection setup.
        target: Destination file path. Parent directory must exist.
        meta: Metadata previously read from the peer.

    Raises:
        TransferError: If a frame or chunk violates the transfer contract
            or the SHA-256 digest does not match ``meta.sha256``.
    """
    digest = hashlib.sha256()
    received = 0
    with target.open("wb") as f:
        while received < meta.size:
            msg_type, payload = await _recv_frame(reader)
            if msg_type != MsgType.FILE_CHUNK:
                msg = "Unexpected frame during file transfer"
                raise ChunkValidationError(msg)
            remaining = meta.size - received
            if not payload:
                msg = "Received empty file chunk"
                raise ChunkValidationError(msg)
            if len(payload) > remaining:
                msg = "Received chunk exceeds declared file size"
                raise ChunkValidationError(msg)
            await asyncio.to_thread(f.write, payload)
            digest.update(payload)
            received += len(payload)

    computed_hash = digest.hexdigest()
    if computed_hash != meta.sha256:
        msg = "SHA-256 mismatch after transfer"
        raise TransferIntegrityError(msg)

    return computed_hash


async def recv_file(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, dest_root: Path
) -> Path:
    """Receive one file announced by a meta frame followed by chunk frames.

    Reads the meta frame, re-anchors ``meta.path`` under ``dest_root``
    (rejecting absolute paths and ``..`` traversal), creates any missing
    parent directories, then receives and verifies the body via
    ``_recv_and_verify_chunks``. After successful verification, sends
    the computed hash back to sender via FILE_VERIFY message for
    bidirectional verification. Removes a partial target on any failure.

    Args:
        reader: Authenticated asyncio stream from the connection setup.
        writer: Authenticated asyncio stream from the connection setup.
        dest_root: Destination folder root. Must exist.

    Returns:
        Absolute path of the written file.

    Raises:
        TransferError: If frames arrive in the wrong order, the meta path
            tries to escape ``dest_root``, or the SHA-256 digest of the
            received bytes does not match ``meta.sha256``.
    """
    msg_type, data = await _recv_frame(reader)
    if msg_type != MsgType.FILE_META:
        msg = "Missing file meta frame"
        raise TransferIntegrityError(msg)
    meta = FileMeta.from_yaml(data)

    target = _safe_resolve_under(dest_root, meta.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        computed_hash = await _recv_and_verify_chunks(reader, target, meta)

        await async_send_msg(writer, MsgType.FILE_VERIFY, computed_hash.encode("utf-8"))
    except BaseException:
        target.unlink(missing_ok=True)
        raise

    return target.resolve()
