from __future__ import annotations

import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from dsync.trust import TrustStore


def create_server_ssl_context(
    cert_path: Path,
    key_path: Path,
    trust_store: TrustStore,
) -> ssl.SSLContext:
    """Create a mutual-TLS server context.

    Requires connecting clients to present a certificate from the trust store.
    Raises ValueError when no trusted peers are registered yet.
    """
    trusted_pem = trust_store.trusted_certs_pem()
    if not trusted_pem:
        raise ValueError(
            "No trusted peers registered — add at least one peer certificate first"
        )

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=trusted_pem.decode())
    return ctx


def create_client_ssl_context(
    cert_path: Path,
    key_path: Path,
    peer_cert: x509.Certificate,
) -> ssl.SSLContext:
    """Create a mutual-TLS client context pinned to a single trusted peer certificate.

    Presents the local certificate for server-side verification and validates the
    server certificate against the provided peer certificate only (certificate pinning).
    """
    peer_pem = peer_cert.public_bytes(serialization.Encoding.PEM).decode()

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=peer_pem)
    return ctx


def verify_peer_cert(raw_der: bytes, trust_store: TrustStore) -> bool:
    """Return True if the DER-encoded peer certificate is in the trust store.

    Use this for post-handshake verification when the SSL context itself
    cannot perform trust-store checks (e.g. on the server side for incoming
    connections from unknown peers during a pairing flow).
    """
    cert = x509.load_der_x509_certificate(raw_der)
    return trust_store.is_trusted(cert)
