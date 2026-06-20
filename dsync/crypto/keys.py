"""Cryptographic key management for Ed25519 keypairs."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import platform
from typing import Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from dsync.crypto.secure_storage import KeyExistsError, SecureKeyStorage

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


def _resolve_key_paths(
    private_path: str | Path | None, public_path: str | Path | None
) -> tuple[Path, Path]:
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
        raise TypeError("Only Ed25519 public keys are supported")

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


def is_valid_fingerprint(fp: str) -> bool:
    """Return True if ``fp`` looks like a supported fingerprint string.

    Accepted forms:
    - "hex-" prefix followed by 64 hex chars
    - "b64u-" prefix followed by urlsafe base64 (padding removed) of 32 bytes
    - raw 64-char hex string
    """
    if not isinstance(fp, str) or not fp:
        return False

    if fp.startswith("hex-"):
        hexpart = fp[4:]
        return len(hexpart) == 64 and all(c in "0123456789abcdefABCDEF" for c in hexpart)

    if fp.startswith("b64u-"):
        b64part = fp[5:]
        padded = b64part + ("=" * (-len(b64part) % 4))
        try:
            decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        except Exception:
            return False
        return len(decoded) == 32

    return len(fp) == 64 and all(c in "0123456789abcdefABCDEF" for c in fp)


def generate_and_store_keypair_securely(
    force: bool = False,
    warn_on_existing: bool = True,
    service_name: str = "dsync",
) -> tuple[str, str]:
    """Generate and store keypair in system keystore.

    Args:
        force: Overwrite existing keys if True
        warn_on_existing: Show warning when keys exist
        service_name: Keystore service name

    Returns:
        (private_key_pem, public_key_pem) as strings

    Raises:
        KeyExistsError: Keys exist and force=False
        KeyringError: Keystore backend failed
    """
    private_key_pem, public_key_pem = generate_keypair()

    storage = SecureKeyStorage(service_name)
    storage.store_keypair(
        private_key_pem.decode("utf-8"),
        public_key_pem.decode("utf-8"),
        force=force,
        warn_on_existing=warn_on_existing,
    )

    return private_key_pem.decode("utf-8"), public_key_pem.decode("utf-8")


def load_keypair_securely(
    service_name: str = "dsync",
) -> tuple[bytes, bytes] | None:
    """Load keypair from system keystore.

    Args:
        service_name: Keystore service name

    Returns:
        (private_key_pem, public_key_pem) as bytes, or None

    Raises:
        KeyringError: Keystore backend failed
    """
    storage = SecureKeyStorage(service_name)
    private_key_str, public_key_str = storage.get_keypair()

    if private_key_str is None or public_key_str is None:
        return None

    return private_key_str.encode("utf-8"), public_key_str.encode("utf-8")


def has_stored_keypair(service_name: str = "dsync") -> bool:
    """Check if keypair exists in keystore.

    Args:
        service_name: Keystore service name

    Returns:
        True if both keys exist, False otherwise
    """
    storage = SecureKeyStorage(service_name)
    return storage.has_keypair()


def delete_stored_keypair(service_name: str = "dsync") -> tuple[bool, bool]:
    """Delete keypair from keystore.

    Args:
        service_name: Keystore service name

    Returns:
        (private_deleted, public_deleted) success flags

    Raises:
        KeyringError: Keystore backend failed
    """
    storage = SecureKeyStorage(service_name)
    return storage.delete_keypair()


def migrate_keys_to_secure_storage(
    private_path: str | Path | None = None,
    public_path: str | Path | None = None,
    force: bool = False,
    service_name: str = "dsync",
) -> tuple[bool, bool]:
    """Migrate file-based keys to secure keystore.

    Args:
        private_path: Path to private key file
        public_path: Path to public key file
        force: Overwrite existing keys in keystore
        service_name: Keystore service name

    Returns:
        (private_migrated, public_migrated) success flags

    Raises:
        KeyExistsError: Keys exist in keystore and force=False
        KeyringError: Keystore backend failed
        FileNotFoundError: Source files don't exist
    """
    storage = SecureKeyStorage(service_name)

    private_migrated = False
    public_migrated = False

    # Migrate private key
    try:
        private_target, _ = _resolve_key_paths(private_path, None)
        if private_target.exists():
            private_key_pem = private_target.read_bytes()
            storage.store_private_key(
                private_key_pem.decode("utf-8"),
                force=force,
                warn_on_existing=False,
            )
            private_migrated = True
    except (KeyExistsError, FileNotFoundError):
        pass

    # Migrate public key
    try:
        _, public_target = _resolve_key_paths(None, public_path)
        if public_target.exists():
            public_key_pem = public_target.read_bytes()
            storage.store_public_key(
                public_key_pem.decode("utf-8"),
                force=force,
                warn_on_existing=False,
            )
            public_migrated = True
    except (KeyExistsError, FileNotFoundError):
        pass

    return private_migrated, public_migrated
