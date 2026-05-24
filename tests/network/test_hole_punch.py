"""Loopback tests for dsync.network.hole_punch.

We can't simulate real NAT on a single host, but we *can* exercise:
* the burst-then-QUIC mechanics,
* the dialer / listener split,
* the retry-once timeout path,
all over BYO UDP sockets bound to loopback. The simulated-NAT proof
(Docker + iptables MASQUERADE) lands in the CI integration step from the
implementation plan; that one validates the actual traversal.
"""

from __future__ import annotations

import asyncio
import datetime
import socket
from typing import TYPE_CHECKING

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from dsync.network.hole_punch import HolePunchError, do_hole_punch
from dsync.network.quic_core import build_quic_configuration

if TYPE_CHECKING:
    from pathlib import Path


def _write_self_signed(cert_path: Path, key_path: Path) -> None:
    """Write a fresh RSA-2048 self-signed cert + PEM key for QUIC TLS."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dsync-test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _bound_udp_socket() -> socket.socket:
    """Bind a fresh UDP socket on loopback and return it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    return sock


@pytest.fixture
def cert_pair(tmp_path: Path) -> tuple[Path, Path]:
    """One throw-away self-signed cert + key pair (used by both peers)."""
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    _write_self_signed(cert, key)
    return cert, key


async def test_dialer_and_listener_complete_handshake(
    cert_pair: tuple[Path, Path],
) -> None:
    """Two BYO sockets, dialer + listener via do_hole_punch, both complete TLS."""
    cert, key = cert_pair
    dialer_sock = _bound_udp_socket()
    listener_sock = _bound_udp_socket()
    listener_addr = listener_sock.getsockname()
    dialer_addr = dialer_sock.getsockname()

    dialer_cfg = build_quic_configuration(is_client=True, cert_path=cert, key_path=key)
    listener_cfg = build_quic_configuration(is_client=False, cert_path=cert, key_path=key)

    dialer_task = asyncio.create_task(
        do_hole_punch(
            sock=dialer_sock,
            peer_addr=listener_addr,
            role="dialer",
            configuration=dialer_cfg,
            handshake_timeout=5.0,
        )
    )
    listener_task = asyncio.create_task(
        do_hole_punch(
            sock=listener_sock,
            peer_addr=dialer_addr,
            role="listener",
            configuration=listener_cfg,
            handshake_timeout=5.0,
        )
    )

    try:
        dialer_endpoint, listener_endpoint = await asyncio.gather(dialer_task, listener_task)
        # Both protocols should have negotiated a TLS 1.3 session by now.
        assert dialer_endpoint.protocol._quic.tls.key_schedule is not None
        assert listener_endpoint.protocol._quic.tls.key_schedule is not None
    finally:
        for task in (dialer_task, listener_task):
            if not task.done():
                task.cancel()
        for sock in (dialer_sock, listener_sock):
            sock.close()


async def test_dialer_role_with_server_config_is_rejected(
    cert_pair: tuple[Path, Path],
) -> None:
    """`role='dialer'` with is_client=False should fail loudly (not silently)."""
    cert, key = cert_pair
    sock = _bound_udp_socket()
    cfg = build_quic_configuration(is_client=False, cert_path=cert, key_path=key)
    try:
        with pytest.raises(ValueError, match="dialer role requires"):
            await do_hole_punch(
                sock=sock,
                peer_addr=("127.0.0.1", 9999),
                role="dialer",
                configuration=cfg,
            )
    finally:
        sock.close()


async def test_listener_role_with_client_config_is_rejected(
    cert_pair: tuple[Path, Path],
) -> None:
    """`role='listener'` with is_client=True should fail loudly."""
    cert, key = cert_pair
    sock = _bound_udp_socket()
    cfg = build_quic_configuration(is_client=True, cert_path=cert, key_path=key)
    try:
        with pytest.raises(ValueError, match="listener role requires"):
            await do_hole_punch(
                sock=sock,
                peer_addr=("127.0.0.1", 9999),
                role="listener",
                configuration=cfg,
            )
    finally:
        sock.close()


async def test_dialer_gives_up_after_max_attempts(
    cert_pair: tuple[Path, Path],
) -> None:
    """No listener ever appears → HolePunchError after the configured attempts."""
    cert, key = cert_pair
    sock = _bound_udp_socket()
    cfg = build_quic_configuration(is_client=True, cert_path=cert, key_path=key)

    try:
        with pytest.raises(HolePunchError, match="failed after 2 attempts"):
            await do_hole_punch(
                sock=sock,
                peer_addr=("127.0.0.1", 1),  # unreachable port; nothing listening
                role="dialer",
                configuration=cfg,
                handshake_timeout=0.3,
                retry_delay=0.05,
                max_attempts=2,
            )
    finally:
        sock.close()


