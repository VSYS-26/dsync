from __future__ import annotations

import base64
import hashlib
import os
import platform
from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

FingerprintFormat = Literal["hex", "base64url"]


def default_key_paths(app_name: str = "dsync") -> tuple[Path, Path]:
    """Return default private/public key paths for the current operating system."""
    system = platform.system()

    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        base_dir = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        key_dir = base_dir / app_name / "keys"
    elif system == "Darwin":
        key_dir = Path.home() / "Library" / "Application Support" / app_name / "keys"
    else:
        key_dir = Path.home() / ".config" / app_name / "keys"

    return key_dir / "id_ed25519.pem", key_dir / "id_ed25519.pub"


def _resolve_key_paths(private_path: str | Path | None, public_path: str | Path | None) -> tuple[Path, Path]:
    default_private_path, default_public_path = default_key_paths()
    private_target = Path(private_path) if private_path is not None else default_private_path
    public_target = Path(public_path) if public_path is not None else default_public_path
    return private_target, public_target

def generate_keypair() -> tuple[bytes, bytes]:
    """Generate an Ed25519 keypair and return both keys as PEM bytes."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def save_keypair(
    private_key_pem: bytes,
    public_key_pem: bytes,
    private_path: str | Path | None = None,
    public_path: str | Path | None = None,
) -> tuple[Path, Path]:
    """Persist keypair to the given paths or OS-specific default paths."""
    private_target, public_target = _resolve_key_paths(private_path, public_path)

    private_target.parent.mkdir(parents=True, exist_ok=True)
    public_target.parent.mkdir(parents=True, exist_ok=True)

    private_target.write_bytes(private_key_pem)
    public_target.write_bytes(public_key_pem)
    return private_target, public_target


def load_keypair(
    private_path: str | Path | None = None,
    public_path: str | Path | None = None,
) -> tuple[bytes, bytes]:
    """Load keypair from the given paths or OS-specific default paths."""
    private_target, public_target = _resolve_key_paths(private_path, public_path)

    private_key_pem = private_target.read_bytes()
    public_key_pem = public_target.read_bytes()
    return private_key_pem, public_key_pem


def public_key_fingerprint(public_key_pem: bytes, fmt: FingerprintFormat = "hex") -> str:
    """Create a stable fingerprint from the public key (SHA-256 over raw Ed25519 bytes)."""
    public_key = serialization.load_pem_public_key(public_key_pem)
    if not isinstance(public_key, Ed25519PublicKey):
        raise ValueError("Only Ed25519 public keys are supported")

    public_raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    digest = hashlib.sha256(public_raw).digest()

    if fmt == "hex":
        return "hex-" + digest.hex()
    if fmt == "base64url":
        return "b64u-" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    raise ValueError(f"Unsupported fingerprint format: {fmt}")

