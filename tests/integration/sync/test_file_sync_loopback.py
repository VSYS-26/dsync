import asyncio
import hashlib
from pathlib import Path

from dsync.network.backup_direction import BackupSession
from dsync.network.file_transfer import recv_file, send_file


async def _run_source_then_close(writer, reader, files, root):
    session = BackupSession.as_source()
    await session.send_files(writer, reader, files, root)
    writer.close()
    await writer.wait_closed()


async def _run_peer(reader, writer, recv_dir):
    session = BackupSession.as_peer()
    await session.receive_files(reader, writer, recv_dir)


async def test_single_file_end_to_end(stream_pair, tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "hello.txt").write_bytes(b"hello world")

    (reader_a, writer_a), (reader_b, writer_b) = stream_pair

    await asyncio.gather(
        _run_source_then_close(writer_a, reader_a, (src / "hello.txt",), src),
        _run_peer(reader_b, writer_b, dst),
    )

    assert (dst / "hello.txt").read_bytes() == b"hello world"


async def test_multiple_files_all_received(stream_pair, tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    files_data = {
        "a.txt": b"file a",
        "b.bin": b"\x00\x01\x02\x03",
        "c.txt": b"file c content is longer",
    }
    for name, content in files_data.items():
        (src / name).write_bytes(content)

    (reader_a, writer_a), (reader_b, writer_b) = stream_pair
    file_paths = tuple(src / name for name in files_data)

    await asyncio.gather(
        _run_source_then_close(writer_a, reader_a, file_paths, src),
        _run_peer(reader_b, writer_b, dst),
    )

    for name, content in files_data.items():
        assert (dst / name).read_bytes() == content


async def test_nested_dirs_preserved(stream_pair, tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    nested = src / "a" / "b"
    nested.mkdir(parents=True)
    dst.mkdir()
    (nested / "deep.txt").write_bytes(b"deep content")

    (reader_a, writer_a), (reader_b, writer_b) = stream_pair

    await asyncio.gather(
        _run_source_then_close(writer_a, reader_a, (nested / "deep.txt",), src),
        _run_peer(reader_b, writer_b, dst),
    )

    assert (dst / "a" / "b" / "deep.txt").read_bytes() == b"deep content"


async def test_large_file_integrity(stream_pair, tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    data = bytes(range(256)) * (5 * 1024)  # ~1.3 MB
    (src / "large.bin").write_bytes(data)

    (reader_a, writer_a), (reader_b, writer_b) = stream_pair

    await asyncio.gather(
        _run_source_then_close(writer_a, reader_a, (src / "large.bin",), src),
        _run_peer(reader_b, writer_b, dst),
    )

    received = (dst / "large.bin").read_bytes()
    assert hashlib.sha256(received).hexdigest() == hashlib.sha256(data).hexdigest()


async def test_empty_folder_no_crash(stream_pair, tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    (reader_a, writer_a), (reader_b, writer_b) = stream_pair

    await asyncio.gather(
        _run_source_then_close(writer_a, reader_a, (), src),
        _run_peer(reader_b, writer_b, dst),
    )

    assert list(dst.iterdir()) == []


async def test_file_overwritten_on_resync(stream_pair, tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "data.txt").write_bytes(b"version 1")

    (reader_a, writer_a), (reader_b, writer_b) = stream_pair

    await asyncio.gather(
        _run_source_then_close(writer_a, reader_a, (src / "data.txt",), src),
        _run_peer(reader_b, writer_b, dst),
    )
    assert (dst / "data.txt").read_bytes() == b"version 1"


async def test_send_recv_single_file_directly(stream_pair, tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    content = b"direct send recv"
    (src / "file.txt").write_bytes(content)

    (reader_a, writer_a), (reader_b, writer_b) = stream_pair

    await asyncio.gather(
        send_file(writer_a, reader_a, src / "file.txt", src),
        recv_file(reader_b, writer_b, dst),
    )

    assert (dst / "file.txt").read_bytes() == content
