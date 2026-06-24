"""Async file transfer over an authenticated asyncio TLS stream."""

import asyncio
from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path, PurePosixPath
import tempfile
import zipfile

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
import yaml

from dsync.config import FolderEntry
from dsync.integrity import compute_sha256
from dsync.network.errors import (
    ChunkValidationError,
    FrameValidationError,
    TransferIntegrityError,
)
from dsync.network.p2p_core import MsgType, async_recv_msg, async_send_msg
from dsync.network.sync_errors import (
    ErrorCode,
    PeerReportedError,
    SyncError,
    notify_peer,
)

logger = logging.getLogger(__name__)

_progress_console = Console(stderr=True)

DEFAULT_CHUNK_SIZE = 64 * 1024
MAX_META_SIZE = 8 * 1024
MAX_CHUNK_SIZE = DEFAULT_CHUNK_SIZE


def _transfer_progress() -> Progress:
    """Build a Rich Progress bar configured for byte-level file transfers."""
    return Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=_progress_console,
        transient=True,
    )


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

    if msg_type == MsgType.ERROR:
        # Peer is aborting the transfer and told us why — surface verbatim.
        raise PeerReportedError(SyncError.from_yaml(payload))

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

    try:
        with path.open("rb") as f, _transfer_progress() as progress:
            task_id = progress.add_task(f"Sending {path.name}", total=meta.size)
            while chunk := await asyncio.to_thread(f.read, DEFAULT_CHUNK_SIZE):
                await async_send_msg(writer, MsgType.FILE_CHUNK, chunk)
                progress.update(task_id, advance=len(chunk))
    except (ConnectionError, BrokenPipeError):
        # Receiver likely aborted with an ERROR frame and closed before we
        # finished sending. Try one short read so we can surface its reason
        # instead of a generic "connection reset".
        try:
            mt, payload = await asyncio.wait_for(async_recv_msg(reader), timeout=2.0)
            if mt == MsgType.ERROR and payload is not None:
                raise PeerReportedError(SyncError.from_yaml(payload)) from None
        except (TimeoutError, ConnectionError, asyncio.IncompleteReadError):
            pass
        raise

    msg_type, peer_hash_bytes = await async_recv_msg(reader)
    if msg_type is None or peer_hash_bytes is None:
        msg = "Connection closed before receiving peer hash verification"
        raise TransferIntegrityError(msg)
    if msg_type == MsgType.ERROR:
        raise PeerReportedError(SyncError.from_yaml(peer_hash_bytes))
    if msg_type != MsgType.FILE_VERIFY:
        msg = f"Expected FILE_VERIFY (type {MsgType.FILE_VERIFY}), got type {msg_type}"
        raise TransferIntegrityError(msg)

    peer_hash = peer_hash_bytes.decode("utf-8")
    if peer_hash != digest:
        raise TransferIntegrityError(f"Hash mismatch: source={digest}, peer={peer_hash}")
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
    with target.open("wb") as f, _transfer_progress() as progress:
        task_id = progress.add_task(f"Receiving {target.name}", total=meta.size)
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
            progress.update(task_id, advance=len(payload))

    computed_hash = digest.hexdigest()
    if computed_hash != meta.sha256:
        msg = "SHA-256 mismatch after transfer"
        raise TransferIntegrityError(msg)

    return computed_hash


async def _recv_file_body(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, dest_root: Path
) -> tuple[Path, str]:
    """Receive a file but do NOT send FILE_VERIFY; return (path, computed_hash).

    Lets the caller perform extra validation (e.g. zip check) before
    acknowledging — so an ERROR frame sent after that validation still
    arrives at the sender ahead of the FILE_VERIFY ack.
    """
    msg_type, data = await _recv_frame(reader)
    if msg_type != MsgType.FILE_META:
        msg = "Missing file meta frame"
        raise TransferIntegrityError(msg)
    meta = FileMeta.from_yaml(data)

    try:
        target = _safe_resolve_under(dest_root, meta.path)
    except TransferIntegrityError as exc:
        await notify_peer(
            writer,
            SyncError(code=ErrorCode.PATH_TRAVERSAL, message=f"{exc} (path={meta.path!r})"),
        )
        raise

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        computed_hash = await _recv_and_verify_chunks(reader, target, meta)
    except PeerReportedError:
        target.unlink(missing_ok=True)
        raise
    except TransferIntegrityError as exc:
        target.unlink(missing_ok=True)
        await notify_peer(writer, SyncError(code=ErrorCode.HASH_MISMATCH, message=str(exc)))
        raise
    except ChunkValidationError as exc:
        target.unlink(missing_ok=True)
        code = ErrorCode.EMPTY_CHUNK if "empty" in str(exc).lower() else ErrorCode.CHUNK_OVERRUN
        await notify_peer(writer, SyncError(code=code, message=str(exc)))
        raise
    except PermissionError as exc:
        target.unlink(missing_ok=True)
        await notify_peer(
            writer,
            SyncError(code=ErrorCode.PERMISSION_DENIED, message=f"receiver cannot write: {exc}"),
        )
        raise
    except OSError as exc:
        target.unlink(missing_ok=True)
        code = ErrorCode.DISK_FULL if getattr(exc, "errno", None) == 28 else ErrorCode.PERMISSION_DENIED
        await notify_peer(writer, SyncError(code=code, message=str(exc)))
        raise
    except BaseException:
        target.unlink(missing_ok=True)
        raise

    return target.resolve(), computed_hash


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
    target, computed_hash = await _recv_file_body(reader, writer, dest_root)
    await async_send_msg(writer, MsgType.FILE_VERIFY, computed_hash.encode("utf-8"))
    return target


async def send_path_as_zip(
    writer: asyncio.StreamWriter,
    reader: asyncio.StreamReader,
    entry: FolderEntry,
) -> None:
    """Pack entry.path (file or folder) into a temp zip and send it."""
    src = Path(entry.path)
    if not src.exists():
        await notify_peer(
            writer,
            SyncError(
                code=ErrorCode.SOURCE_MISSING,
                message=f"Source path does not exist on sender: {src}",
            ),
        )
        raise FileNotFoundError(f"Source not found: {src}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        zip_path = tmpdir_path / f"{entry.id}.zip"

        def _make_zip() -> None:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                if src.is_file():
                    zf.write(src, src.name)
                else:
                    iterator = src.rglob("*") if entry.recursive else src.glob("*")
                    for p in iterator:
                        if p.is_file():
                            arcname = p.relative_to(src).as_posix()
                            zf.write(p, arcname)
                    if entry.recursive:
                        for d in src.rglob("*"):
                            if d.is_dir() and not any(d.iterdir()):
                                zf.writestr(d.relative_to(src).as_posix() + "/", "")

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            console=_progress_console,
            transient=True,
        ) as progress:
            progress.add_task(f"Packing {src.name}…", total=None)
            await asyncio.to_thread(_make_zip)
        logger.info(f"[*] packed {src} -> {zip_path.name}")
        await send_file(writer, reader, zip_path, tmpdir_path)


async def recv_folder_and_extract(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    dest_root: Path,
) -> None:
    """Receive a zip file and extract it into dest_root."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        zip_path, computed_hash = await _recv_file_body(reader, writer, tmpdir_path)

        if not zipfile.is_zipfile(zip_path):
            # Send ERROR *before* FILE_VERIFY so the sender, which is still
            # waiting on its post-chunk recv, actually reads our reason.
            await notify_peer(
                writer,
                SyncError(
                    code=ErrorCode.BAD_ZIP,
                    message="Received file is not a valid zip archive",
                ),
            )
            raise TransferIntegrityError("Received file is not a valid zip archive")

        await async_send_msg(writer, MsgType.FILE_VERIFY, computed_hash.encode("utf-8"))

        def _extract() -> None:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(dest_root)

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            console=_progress_console,
            transient=True,
        ) as progress:
            progress.add_task(f"Extracting to {dest_root}…", total=None)
            await asyncio.to_thread(_extract)
        logger.info(f"[+] Extracted zip to {dest_root}")
