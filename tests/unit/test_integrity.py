import hashlib
from pathlib import Path

from dsync.integrity import compute_sha256


def test_known_content(tmp_path: Path) -> None:
    f = tmp_path / "file.bin"
    f.write_bytes(b"hello world")
    assert compute_sha256(f) == hashlib.sha256(b"hello world").hexdigest()


def test_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "empty.bin"
    f.write_bytes(b"")
    assert compute_sha256(f) == hashlib.sha256(b"").hexdigest()


def test_large_file(tmp_path: Path) -> None:
    data = b"x" * (3 * 1024 * 1024)
    f = tmp_path / "large.bin"
    f.write_bytes(data)
    assert compute_sha256(f) == hashlib.sha256(data).hexdigest()


def test_accepts_string_path(tmp_path: Path) -> None:
    f = tmp_path / "file.bin"
    f.write_bytes(b"test")
    result = compute_sha256(str(f))
    assert result == hashlib.sha256(b"test").hexdigest()


def test_digest_is_lowercase_hex(tmp_path: Path) -> None:
    f = tmp_path / "file.bin"
    f.write_bytes(b"abc")
    digest = compute_sha256(f)
    assert digest == digest.lower()
    assert all(c in "0123456789abcdef" for c in digest)
    assert len(digest) == 64
