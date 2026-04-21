from __future__ import annotations

from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from dsync.identity import cert_fingerprint

_TRUST_DIR = Path.home() / ".config" / "dsync" / "trust"


class TrustStore:
    """Persistent store of trusted peer certificates for P2P trust verification."""

    def __init__(self, trust_dir: Path = _TRUST_DIR) -> None:
        self._dir = trust_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, fingerprint: str) -> Path:
        return self._dir / f"{fingerprint}.pem"

    def add(self, cert: x509.Certificate) -> str:
        """Persist a peer certificate as trusted. Returns its fingerprint."""
        fp = cert_fingerprint(cert)
        self._path(fp).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return fp

    def remove(self, fingerprint: str) -> None:
        """Remove a trusted peer certificate by fingerprint."""
        path = self._path(fingerprint)
        if path.exists():
            path.unlink()

    def is_trusted(self, cert: x509.Certificate) -> bool:
        """Return True if the certificate fingerprint is in the trust store."""
        return self._path(cert_fingerprint(cert)).exists()

    def list_fingerprints(self) -> list[str]:
        """Return fingerprints of all trusted peer certificates."""
        return [p.stem for p in sorted(self._dir.glob("*.pem"))]

    def load_cert(self, fingerprint: str) -> x509.Certificate:
        """Load a trusted certificate by fingerprint."""
        return x509.load_pem_x509_certificate(self._path(fingerprint).read_bytes())

    def trusted_certs_pem(self) -> bytes:
        """Return all trusted certificates concatenated as PEM bytes."""
        return b"".join(self._path(fp).read_bytes() for fp in self.list_fingerprints())
