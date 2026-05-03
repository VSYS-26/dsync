"""File integrity helpers."""

import hashlib
from pathlib import Path


def compute_sha256(file_path: str | Path) -> str:
    """Return the SHA-256 hex digest of the file at ``file_path``."""
    path = Path(file_path)
    with path.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256")
    return digest.hexdigest()
