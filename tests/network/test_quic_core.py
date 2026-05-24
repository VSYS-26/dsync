"""Tests for ``dsync.network.quic_core`` — QUIC channel-binding derivation.

The key invariant proven here: after a successful QUIC handshake between two
peers, both peers compute the same 32-byte session-unique value when they call
``get_quic_channel_binding``. That value is what each peer signs in the AUTH
frame to prove identity (MITM-via-relay resistance).
"""

from __future__ import annotations

import asyncio
import datetime
from typing import TYPE_CHECKING, Any

from aioquic.asyncio import connect, serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from dsync.network.quic_core import (
    CHANNEL_BINDING_LENGTH,
    build_quic_configuration,
    get_quic_channel_binding,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_self_signed(cert_path: Path, key_path: Path) -> None:
    """Write a throw-away RSA-2048 self-signed cert + PEM key to disk."""
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


@pytest.fixture
def cert_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Provide a fresh self-signed (cert, key) pair in a temp dir."""
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    _write_self_signed(cert, key)
    return cert, key


async def test_channel_binding_matches_on_both_peers(
    cert_pair: tuple[Path, Path],
) -> None:
    """Both peers compute the same 32-byte channel binding after the handshake."""
    cert, key = cert_pair
    server_config = build_quic_configuration(is_client=False, cert_path=cert, key_path=key)
    client_config = build_quic_configuration(is_client=True, cert_path=cert, key_path=key)

    captured_server_protocols: list[QuicConnectionProtocol] = []

    class CapturingProtocol(QuicConnectionProtocol):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            captured_server_protocols.append(self)

    server_endpoint = await serve(
        host="127.0.0.1",
        port=0,
        configuration=server_config,
        create_protocol=CapturingProtocol,
    )
    try:
        port = server_endpoint._transport.get_extra_info("sockname")[1]
        async with connect(
            host="127.0.0.1",
            port=port,
            configuration=client_config,
        ) as client:
            # Force a round-trip so the server completes its half of the handshake.
            await client.ping()

            # Wait briefly until the server registers the new connection.
            for _ in range(100):
                if captured_server_protocols:
                    break
                await asyncio.sleep(0.01)
            assert captured_server_protocols, "server never accepted a connection"
            server = captured_server_protocols[0]

            client_binding = get_quic_channel_binding(client._quic)
            server_binding = get_quic_channel_binding(server._quic)

            assert len(client_binding) == CHANNEL_BINDING_LENGTH
            assert len(server_binding) == CHANNEL_BINDING_LENGTH
            assert client_binding == server_binding
    finally:
        server_endpoint.close()


async def test_channel_binding_unavailable_before_handshake(
    cert_pair: tuple[Path, Path],
) -> None:
    """Calling get_quic_channel_binding before the handshake raises cleanly."""
    from aioquic.quic.connection import QuicConnection

    cert, key = cert_pair
    config = build_quic_configuration(is_client=True, cert_path=cert, key_path=key)
    connection = QuicConnection(configuration=config)
    with pytest.raises(RuntimeError, match="handshake has not completed"):
        get_quic_channel_binding(connection)


async def test_distinct_sessions_produce_distinct_bindings(
    tmp_path: Path,
) -> None:
    """Channel bindings from two independent sessions must differ."""
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    _write_self_signed(cert, key)

    async def one_session() -> bytes:
        server_config = build_quic_configuration(is_client=False, cert_path=cert, key_path=key)
        client_config = build_quic_configuration(is_client=True, cert_path=cert, key_path=key)
        captured: list[QuicConnectionProtocol] = []

        class CapturingProtocol(QuicConnectionProtocol):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                captured.append(self)

        endpoint = await serve(
            host="127.0.0.1",
            port=0,
            configuration=server_config,
            create_protocol=CapturingProtocol,
        )
        try:
            port = endpoint._transport.get_extra_info("sockname")[1]
            async with connect(
                host="127.0.0.1",
                port=port,
                configuration=client_config,
            ) as client:
                await client.ping()
                for _ in range(100):
                    if captured:
                        break
                    await asyncio.sleep(0.01)
                assert captured
                return get_quic_channel_binding(client._quic)
        finally:
            endpoint.close()

    binding_a = await one_session()
    binding_b = await one_session()
    assert binding_a != binding_b
