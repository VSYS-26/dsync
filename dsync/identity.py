from __future__ import annotations

import datetime
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.x509.oid import NameOID

_CONFIG_DIR = Path.home() / ".config" / "dsync"
_IDENTITY_DIR = _CONFIG_DIR / "identity"
_CERT_FILE = _IDENTITY_DIR / "cert.pem"
_KEY_FILE = _IDENTITY_DIR / "key.pem"

_KEY_SIZE = 4096
_CERT_VALIDITY_DAYS = 3650


def generate_identity() -> tuple[RSAPrivateKey, x509.Certificate]:
    """Generate a new RSA key pair and self-signed certificate and persist them to disk."""
    _IDENTITY_DIR.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=_KEY_SIZE)

    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dsync-device")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=_CERT_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    fd = os.open(str(_KEY_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key_pem)
    finally:
        os.close(fd)

    _CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    return key, cert


def load_identity() -> tuple[RSAPrivateKey, x509.Certificate]:
    """Load the persisted RSA key and certificate from disk."""
    raw_key = serialization.load_pem_private_key(_KEY_FILE.read_bytes(), password=None)
    if not isinstance(raw_key, RSAPrivateKey):
        raise TypeError("Identity key is not RSA")
    cert = x509.load_pem_x509_certificate(_CERT_FILE.read_bytes())
    return raw_key, cert


def get_or_create_identity() -> tuple[RSAPrivateKey, x509.Certificate]:
    """Return the existing identity or generate a new one if none exists."""
    if _CERT_FILE.exists() and _KEY_FILE.exists():
        return load_identity()
    return generate_identity()


def cert_fingerprint(cert: x509.Certificate) -> str:
    """Return the SHA-256 fingerprint of a certificate as a lowercase hex string."""
    digest: bytes = cert.fingerprint(hashes.SHA256())
    return digest.hex()
