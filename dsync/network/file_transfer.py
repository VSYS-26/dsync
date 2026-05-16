"""Async file transfer over an authenticated asyncio TLS stream."""

import asyncio
from dataclasses import dataclass
import hashlib
from pathlib import Path

import yaml

from dsync.integrity import compute_sha256
from dsync.network.errors import (
    ChunkValidationError,
    FrameValidationError,
    TransferIntegrityError,
)
from dsync.network.p2p_core import MsgType, async_recv_msg, async_send_msg

DEFAULT_CHUNK_SIZE = 64 * 1024
MAX_META_SIZE = 8 * 1024
MAX_CHUNK_SIZE = DEFAULT_CHUNK_SIZE


@dataclass(frozen=True)
class FileMeta:
    """Metadata announced by the sender before chunk frames arrive.

    Attributes:
        name: File basename. Used by the receiver to derive the destination
            path under its target directory.
        size: Total file size in bytes. Used as the stop condition for the
            receiver's chunk loop.
        sha256: Hex SHA-256 digest of the full file. Verified by the
            receiver after all chunks have been written.
    """

    name: str
    size: int
    sha256: str

    @classmethod
    def from_yaml(cls, data: bytes) -> "FileMeta":
        """Parse a YAML-encoded meta payload into a ``FileMeta`` instance."""
        raw = yaml.safe_load(data.decode("utf-8"))
        return cls(name=raw["name"], size=raw["size"], sha256=raw["sha256"])

    def to_yaml(self) -> bytes:
        """Serialize this metadata as a YAML-encoded byte payload."""
        return yaml.dump(
            {"name": self.name, "size": self.size, "sha256": self.sha256},
        ).encode("utf-8")


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


async def send_file(writer: asyncio.StreamWriter, reader: asyncio.StreamReader, path: Path) -> None:
    """Send one file over an open asyncio TLS stream in chunks.

    Sends a YAML meta frame (name, size, sha256) followed by raw chunk
    frames of ``DEFAULT_CHUNK_SIZE`` bytes until the file is exhausted.

    Args:
        writer: Authenticated asyncio stream from the connection setup.
        reader: Authenticated asyncion stream from the connection setup.
        path: Source file to transmit.

    Raises:
        FileNotFoundError: If ``path`` does not exist or is not a regular file.
        TransferIntegrityError: If peer's hash verification fails or wrong message type received.
    """
    if not path.is_file():
        msg = f"Not a regular file: {path}"
        raise FileNotFoundError(msg)

    digest = await asyncio.to_thread(compute_sha256, path)
    meta = FileMeta(name=path.name, size=path.stat().st_size, sha256=digest)
    await async_send_msg(writer, MsgType.FILE_META, meta.to_yaml())

    with path.open("rb") as f:
        while chunk := await asyncio.to_thread(f.read, DEFAULT_CHUNK_SIZE):
            await async_send_msg(writer, MsgType.FILE_CHUNK, chunk)

    msg_type, peer_hash_bytes = await async_recv_msg(reader)
    if msg_type is None or peer_hash_bytes is None:
        msg = "Connection closed before receiving peer hash verification"
        raise TransferIntegrityError(msg)
    if msg_type != MsgType.FILE_VERIFY:
        msg = f"Excpected FILE_VERIFY (type {MsgType.FILE_VERIFY}), got type {msg_type}"
        raise TransferIntegrityError(msg)
    
    peer_hash = peer_hash_bytes.decode('utf-8')
    if peer_hash != digest:
        print(f"[!] WARNING: Hash mismatch for file '{path.name}'")
        print(f"    Source hash: {digest}")
        print(f"    Peer hash: {peer_hash}")
    else:
        print(f"[+] Verified: {path.name} (hash match confirmed my peer)")

async def _recv_and_verify_chunks(
    reader: asyncio.StreamReader, target: Path, meta: FileMeta
) -> None:
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


async def recv_file(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, target_dir: Path) -> Path:
    """Receive one file announced by a meta frame followed by chunk frames.

    Reads the meta frame, derives the destination path from ``target_dir``
    and ``meta.name`` (basename only), then receives and verifies the body
    via ``_recv_and_verify_chunks``. After a successful verification, sends
    the computed hash back to sender via FILE_VERIFY message for bidirectional
    verification. Removes a partial target on any failure.

    Args:
        reader: Authenticated asyncio stream from the connection setup.
        writer: Authenticated asyncio stream from the connection setup.
        target_dir: Destination directory. Must exist.

    Returns:
        Absolute path of the written file.

    Raises:
        TransferError: If frames arrive in the wrong order or the SHA-256
            digest of the received bytes does not match ``meta.sha256``.
    """
    msg_type, data = await _recv_frame(reader)
    if msg_type != MsgType.FILE_META:
        msg = "Missing file meta frame"
        raise TransferIntegrityError(msg)
    meta = FileMeta.from_yaml(data)

    target = target_dir / Path(meta.name).name
    try:
        computed_hash = await _recv_and_verify_chunks(reader, target, meta)

        await async_send_msg(writer, MsgType.FILE_VERIFY, computed_hash.encode('utf-8'))
    except BaseException:
        target.unlink(missing_ok=True)
        raise

    return target.resolve()
