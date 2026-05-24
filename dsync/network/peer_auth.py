"""Transport-agnostic helpers for SPKI/signature peer authentication.

Both the relay control channel and the peer-to-peer data channel sign the
session's channel-binding bytes with the local RSA key and exchange the
signed payload as an AUTH frame. Centralising the helpers here lets the
relay code (PR 3) and the peer-session code (PR 5) share one implementation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_der_public_key,
    load_pem_private_key,
)

#: SubjectPublicKeyInfo size for RSA-2048 in DER encoding.
SPKI_SIZE = 294

#: RSA-2048 PSS signature size in bytes.
SIG_SIZE = 256

#: Fixed-size AUTH-frame payload: SPKI || signature.
AUTH_PAYLOAD_SIZE = SPKI_SIZE + SIG_SIZE


def load_rsa_private_key(key_path: Path | str) -> RSAPrivateKey:
    """Load an RSA-2048 private key from PEM and assert the type/size.

    Args:
        key_path: Filesystem path to the PEM-encoded private key.

    Returns:
        The loaded RSA private key.

    Raises:
        TypeError: If the file does not contain an RSA-2048 key.
    """
    with Path(key_path).open("rb") as f:
        raw = load_pem_private_key(f.read(), password=None)
    if not isinstance(raw, RSAPrivateKey):
        raise TypeError("Only RSA private keys are supported for peer auth")
    if raw.key_size != 2048:
        raise TypeError(f"Only RSA-2048 keys are supported, got RSA-{raw.key_size}")
    return raw


def extract_spki(private_key: RSAPrivateKey) -> bytes:
    """Return the DER-encoded SubjectPublicKeyInfo (294 B for RSA-2048)."""
    return private_key.public_key().public_bytes(
        Encoding.DER,
        PublicFormat.SubjectPublicKeyInfo,
    )


def fingerprint_from_spki(spki: bytes) -> str:
    """Compute the SHA-256 hex fingerprint of an SPKI (matches devices.yaml)."""
    return hashlib.sha256(spki).hexdigest()


def sign_channel_binding(private_key: RSAPrivateKey, channel_binding: bytes) -> bytes:
    """Sign ``channel_binding`` with the local RSA key using PSS/SHA-256."""
    return private_key.sign(
        channel_binding,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )


def verify_signature(
    spki: bytes,
    channel_binding: bytes,
    signature: bytes,
) -> RSAPublicKey:
    """Verify a peer's signature over ``channel_binding`` using the SPKI.

    Args:
        spki: Peer's DER SubjectPublicKeyInfo (294 B for RSA-2048).
        channel_binding: 32 bytes derived from the live QUIC/TLS session.
        signature: 256 B PSS/SHA-256 signature produced by the peer.

    Returns:
        The loaded RSA public key on success.

    Raises:
        ValueError: If the SPKI is not RSA, or the signature does not verify.
    """
    public_key = load_der_public_key(spki)
    if not isinstance(public_key, RSAPublicKey):
        raise ValueError("Peer SPKI must use RSA key")
    try:
        public_key.verify(
            signature,
            channel_binding,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
    except Exception as exc:
        raise ValueError(f"Peer signature invalid: {exc}") from exc
    return public_key


def pack_auth_payload(spki: bytes, signature: bytes) -> bytes:
    """Concatenate SPKI || signature into the fixed-size AUTH payload."""
    if len(spki) != SPKI_SIZE:
        raise ValueError(f"SPKI size mismatch: got {len(spki)}, expected {SPKI_SIZE}")
    if len(signature) != SIG_SIZE:
        raise ValueError(f"Signature size mismatch: got {len(signature)}, expected {SIG_SIZE}")
    return spki + signature


def unpack_auth_payload(payload: bytes) -> tuple[bytes, bytes]:
    """Split a fixed-size AUTH payload back into (SPKI, signature)."""
    if len(payload) != AUTH_PAYLOAD_SIZE:
        raise ValueError(
            f"AUTH payload size mismatch: got {len(payload)}, expected {AUTH_PAYLOAD_SIZE}"
        )
    return payload[:SPKI_SIZE], payload[SPKI_SIZE:]
