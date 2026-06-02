import asyncio
import hashlib
from pathlib import Path

import pytest
import yaml

from dsync.network.errors import ChunkValidationError, TransferIntegrityError
from dsync.network.file_transfer import FileMeta, recv_file, send_file
from dsync.network.p2p_core import MsgType, async_send_msg


async def _send_and_recv(stream_pair, src_path: Path, src_root: Path, dest_root: Path) -> Path:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    await asyncio.gather(
        send_file(writer_a, src_path, src_root),
        recv_file(reader_b, dest_root),
    )
    return dest_root / src_path.relative_to(src_root)


async def test_small_file_content_preserved(stream_pair, tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "file.txt").write_bytes(b"hello world")

    result = await _send_and_recv(stream_pair, src / "file.txt", src, dst)

    assert result.read_bytes() == b"hello world"


async def test_large_file_content_preserved(stream_pair, tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    data = b"A" * (200 * 1024)  # 200 KiB (forces multiple chunks)
    (src / "big.bin").write_bytes(data)

    result = await _send_and_recv(stream_pair, src / "big.bin", src, dst)

    assert result.read_bytes() == data


async def test_empty_file_transfers(stream_pair, tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "empty.bin").write_bytes(b"")

    result = await _send_and_recv(stream_pair, src / "empty.bin", src, dst)

    assert result.read_bytes() == b""


async def test_nested_directory_structure_preserved(stream_pair, tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "sub" / "dir").mkdir(parents=True)
    dst.mkdir()
    (src / "sub" / "dir" / "nested.txt").write_bytes(b"nested")

    result = await _send_and_recv(stream_pair, src / "sub" / "dir" / "nested.txt", src, dst)

    assert result.read_bytes() == b"nested"
    assert result == dst / "sub" / "dir" / "nested.txt"


async def test_sha256_verified_after_transfer(stream_pair, tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    content = b"integrity check"
    (src / "data.bin").write_bytes(content)

    result = await _send_and_recv(stream_pair, src / "data.bin", src, dst)

    assert hashlib.sha256(result.read_bytes()).hexdigest() == hashlib.sha256(content).hexdigest()


async def test_path_traversal_rejected(stream_pair, tmp_path: Path) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    dst = tmp_path / "dst"
    dst.mkdir()

    meta = {"path": "../../etc/passwd", "size": 5, "sha256": "fake"}
    await async_send_msg(writer_a, MsgType.FILE_META, yaml.dump(meta).encode())

    with pytest.raises(TransferIntegrityError):
        await recv_file(reader_b, dst)


async def test_absolute_path_rejected(stream_pair, tmp_path: Path) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    dst = tmp_path / "dst"
    dst.mkdir()

    meta = {"path": "/etc/passwd", "size": 5, "sha256": "fake"}
    await async_send_msg(writer_a, MsgType.FILE_META, yaml.dump(meta).encode())

    with pytest.raises(TransferIntegrityError):
        await recv_file(reader_b, dst)


async def test_tampered_content_raises_integrity_error(stream_pair, tmp_path: Path) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    dst = tmp_path / "dst"
    dst.mkdir()

    original = b"original content"
    correct_sha256 = hashlib.sha256(original).hexdigest()
    meta = {"path": "file.bin", "size": len(original), "sha256": correct_sha256}
    await async_send_msg(writer_a, MsgType.FILE_META, yaml.dump(meta).encode())
    tampered = b"tampered content"
    await async_send_msg(writer_a, MsgType.FILE_CHUNK, tampered)

    with pytest.raises(TransferIntegrityError, match="SHA-256"):
        await recv_file(reader_b, dst)


async def test_send_file_not_found_raises(stream_pair, tmp_path: Path) -> None:
    (_reader_a, writer_a), (_reader_b, _writer_b) = stream_pair
    with pytest.raises(FileNotFoundError):
        await send_file(writer_a, tmp_path / "nonexistent.txt", tmp_path)


def test_file_meta_yaml_roundtrip() -> None:
    meta = FileMeta(path="sub/file.txt", size=42, sha256="a" * 64)
    encoded = meta.to_yaml()
    decoded = FileMeta.from_yaml(encoded)
    assert decoded == meta


# ── _recv_frame edge cases ─────────────────────────────────────────────────────

import struct  # noqa: E402

from dsync.network.errors import FrameValidationError  # noqa: E402
from dsync.network.file_transfer import MAX_CHUNK_SIZE, MAX_META_SIZE  # noqa: E402


async def test_recv_frame_unknown_type_raises(stream_pair, tmp_path: Path) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    unknown_type = 99
    writer_a.write(struct.pack("!BI", unknown_type, 4) + b"data")
    await writer_a.drain()

    from dsync.network.file_transfer import _recv_frame

    with pytest.raises(FrameValidationError, match="Unknown"):
        await _recv_frame(reader_b)


async def test_recv_frame_unsupported_type_raises(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    hello_type = MsgType.HELLO
    writer_a.write(struct.pack("!BI", hello_type, 5) + b"hello")
    await writer_a.drain()

    from dsync.network.file_transfer import _recv_frame

    with pytest.raises(FrameValidationError, match="Unsupported"):
        await _recv_frame(reader_b)


async def test_recv_frame_oversized_file_meta_raises(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    oversized = MAX_META_SIZE + 1
    header = struct.pack("!BI", MsgType.FILE_META, oversized)
    writer_a.write(header + b"x" * oversized)
    await writer_a.drain()

    from dsync.network.file_transfer import _recv_frame

    with pytest.raises(FrameValidationError, match="exceeds"):
        await _recv_frame(reader_b)


async def test_recv_frame_oversized_chunk_raises(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    oversized = MAX_CHUNK_SIZE + 1
    header = struct.pack("!BI", MsgType.FILE_CHUNK, oversized)
    writer_a.write(header + b"x" * oversized)
    await writer_a.drain()

    from dsync.network.file_transfer import _recv_frame

    with pytest.raises(FrameValidationError, match="exceeds"):
        await _recv_frame(reader_b)


async def test_recv_frame_eof_raises_incomplete_read(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    writer_a.close()
    await writer_a.wait_closed()

    from dsync.network.file_transfer import _recv_frame

    with pytest.raises(asyncio.IncompleteReadError):
        await _recv_frame(reader_b)


# ── _recv_and_verify_chunks edge cases ────────────────────────────────────────


async def test_non_chunk_frame_during_transfer_raises(stream_pair, tmp_path: Path) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    dst = tmp_path / "dst"
    dst.mkdir()
    target = dst / "file.bin"

    content = b"hello"
    sha = hashlib.sha256(content).hexdigest()
    meta = FileMeta(path="file.bin", size=len(content), sha256=sha)

    await async_send_msg(
        writer_a,
        MsgType.FILE_META,
        yaml.dump({"path": "file.bin", "size": len(content), "sha256": sha}).encode(),
    )
    await async_send_msg(writer_a, MsgType.FILE_META, meta.to_yaml())

    from dsync.network.file_transfer import _recv_and_verify_chunks

    with pytest.raises(ChunkValidationError, match="Unexpected"):
        await _recv_and_verify_chunks(reader_b, target, meta)


async def test_empty_chunk_raises(stream_pair, tmp_path: Path) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    dst = tmp_path / "dst"
    dst.mkdir()
    target = dst / "file.bin"

    content = b"hello"
    sha = hashlib.sha256(content).hexdigest()
    meta = FileMeta(path="file.bin", size=len(content), sha256=sha)

    await async_send_msg(writer_a, MsgType.FILE_CHUNK, b"")

    from dsync.network.file_transfer import _recv_and_verify_chunks

    with pytest.raises(ChunkValidationError, match="empty"):
        await _recv_and_verify_chunks(reader_b, target, meta)


async def test_oversized_chunk_raises(stream_pair, tmp_path: Path) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    dst = tmp_path / "dst"
    dst.mkdir()
    target = dst / "file.bin"

    content = b"hi"
    sha = hashlib.sha256(content).hexdigest()
    meta = FileMeta(path="file.bin", size=len(content), sha256=sha)

    await async_send_msg(writer_a, MsgType.FILE_CHUNK, b"x" * 100)

    from dsync.network.file_transfer import _recv_and_verify_chunks

    with pytest.raises(ChunkValidationError, match="exceeds"):
        await _recv_and_verify_chunks(reader_b, target, meta)


async def test_recv_file_non_meta_first_frame_raises(stream_pair, tmp_path: Path) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    dst = tmp_path / "dst"
    dst.mkdir()

    await async_send_msg(writer_a, MsgType.FILE_CHUNK, b"x" * 10)

    with pytest.raises(TransferIntegrityError, match="meta"):
        await recv_file(reader_b, dst)
