"""Tests for ``dsync.network.multi_quic.MultiQuicEndpoint``.

The big claim of this module is "one UDP socket can host multiple QUIC
connections at once, routed by source addr" — which is what lets a peer
hold a relay control channel and a peer-to-peer data channel on the same
socket (preserving the NAT mapping the relay observed). The test
``test_two_outgoing_on_one_socket`` is the load-bearing proof of that
claim; the simpler tests pin the routing primitives in isolation.
"""

from __future__ import annotations

import asyncio
import datetime
from typing import TYPE_CHECKING

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from dsync.network.multi_quic import MultiQuicEndpoint
from dsync.network.quic_core import build_quic_configuration

if TYPE_CHECKING:
    from pathlib import Path


def _write_self_signed(cert_path: Path, key_path: Path) -> None:
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
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    _write_self_signed(cert, key)
    return cert, key


async def test_outgoing_and_accept_roundtrip(
    cert_pair: tuple[Path, Path],
) -> None:
    """One server endpoint, one client endpoint: handshake completes both ways."""
    cert, key = cert_pair

    server = await MultiQuicEndpoint.bind(host="127.0.0.1", port=0)
    server.enable_server(
        build_quic_configuration(is_client=False, cert_path=cert, key_path=key),
    )
    client = await MultiQuicEndpoint.bind(host="127.0.0.1", port=0)
    try:
        outgoing = await client.add_outgoing(
            server.local_addr,
            build_quic_configuration(is_client=True, cert_path=cert, key_path=key),
        )
        accepted = await server.accept_next(timeout=5.0)
        await asyncio.gather(outgoing.wait_connected(), accepted.wait_connected())

        assert outgoing._quic.tls.key_schedule is not None
        assert accepted._quic.tls.key_schedule is not None
    finally:
        await client.close()
        await server.close()


async def test_two_outgoing_on_one_socket(
    cert_pair: tuple[Path, Path],
) -> None:
    """One client socket, two outgoing connections to two distinct servers.

    This is the multiplexing claim: routing-by-source-addr works for outgoing
    connections too. The client's transport is single, but datagrams from
    each server reach the right ``QuicConnectionProtocol``.
    """
    cert, key = cert_pair

    server_a = await MultiQuicEndpoint.bind(host="127.0.0.1", port=0)
    server_a.enable_server(
        build_quic_configuration(is_client=False, cert_path=cert, key_path=key),
    )
    server_b = await MultiQuicEndpoint.bind(host="127.0.0.1", port=0)
    server_b.enable_server(
        build_quic_configuration(is_client=False, cert_path=cert, key_path=key),
    )
    client = await MultiQuicEndpoint.bind(host="127.0.0.1", port=0)

    try:
        outgoing_a = await client.add_outgoing(
            server_a.local_addr,
            build_quic_configuration(is_client=True, cert_path=cert, key_path=key),
        )
        outgoing_b = await client.add_outgoing(
            server_b.local_addr,
            build_quic_configuration(is_client=True, cert_path=cert, key_path=key),
        )

        accepted_a = await server_a.accept_next(timeout=5.0)
        accepted_b = await server_b.accept_next(timeout=5.0)

        await asyncio.gather(
            outgoing_a.wait_connected(),
            outgoing_b.wait_connected(),
            accepted_a.wait_connected(),
            accepted_b.wait_connected(),
        )

        # Both connections should be independently healthy: ping each.
        await outgoing_a.ping()
        await outgoing_b.ping()
    finally:
        await client.close()
        await server_a.close()
        await server_b.close()


async def test_outgoing_and_incoming_share_one_socket(
    cert_pair: tuple[Path, Path],
) -> None:
    """A single endpoint runs an outgoing client AND accepts an incoming peer.

    This is the production peer shape: relay control channel goes out;
    peer-to-peer dial-in comes in; both terminate on the same UDP socket.
    """
    cert, key = cert_pair

    relay = await MultiQuicEndpoint.bind(host="127.0.0.1", port=0)
    relay.enable_server(
        build_quic_configuration(is_client=False, cert_path=cert, key_path=key),
    )
    peer = await MultiQuicEndpoint.bind(host="127.0.0.1", port=0)
    peer.enable_server(
        build_quic_configuration(is_client=False, cert_path=cert, key_path=key),
    )
    dialer = await MultiQuicEndpoint.bind(host="127.0.0.1", port=0)

    try:
        # peer "calls" the relay (outgoing). Drive the handshake to completion
        # before starting the second leg — real callers (RelayDaemon) always
        # wait for connection readiness before moving on.
        peer_to_relay = await peer.add_outgoing(
            relay.local_addr,
            build_quic_configuration(is_client=True, cert_path=cert, key_path=key),
        )
        relay_accepts_peer = await relay.accept_next(timeout=5.0)
        await asyncio.gather(
            peer_to_relay.wait_connected(),
            relay_accepts_peer.wait_connected(),
        )

        # NOW dialer dials peer's socket directly. peer's existing relay
        # connection must keep working as the incoming dial is processed
        # on the same socket.
        dialer_to_peer = await dialer.add_outgoing(
            peer.local_addr,
            build_quic_configuration(is_client=True, cert_path=cert, key_path=key),
        )
        peer_accepts_dialer = await peer.accept_next(timeout=5.0)
        await asyncio.gather(
            dialer_to_peer.wait_connected(),
            peer_accepts_dialer.wait_connected(),
        )

        # Both connections are live on peer's single socket.
        assert peer_to_relay._quic.tls.key_schedule is not None
        assert peer_accepts_dialer._quic.tls.key_schedule is not None
        # And the relay sees the peer's source addr — which is the SAME
        # local addr that the dialer reached.
        observed_at_relay = relay_accepts_peer._quic._network_paths[0].addr
        assert observed_at_relay == peer.local_addr
        # peer-to-relay still works after the second connection arrived.
        await peer_to_relay.ping()
    finally:
        await dialer.close()
        await peer.close()
        await relay.close()


async def test_add_outgoing_duplicate_addr_rejected(
    cert_pair: tuple[Path, Path],
) -> None:
    """Two outgoing connections to the same peer addr should not silently overwrite."""
    cert, key = cert_pair
    server = await MultiQuicEndpoint.bind(host="127.0.0.1", port=0)
    server.enable_server(
        build_quic_configuration(is_client=False, cert_path=cert, key_path=key),
    )
    client = await MultiQuicEndpoint.bind(host="127.0.0.1", port=0)
    try:
        await client.add_outgoing(
            server.local_addr,
            build_quic_configuration(is_client=True, cert_path=cert, key_path=key),
        )
        with pytest.raises(ValueError, match="already attached"):
            await client.add_outgoing(
                server.local_addr,
                build_quic_configuration(is_client=True, cert_path=cert, key_path=key),
            )
    finally:
        await client.close()
        await server.close()


async def test_accept_without_server_mode_raises(
    cert_pair: tuple[Path, Path],
) -> None:
    """accept_next must error when the endpoint has not opted into server mode."""
    endpoint = await MultiQuicEndpoint.bind(host="127.0.0.1", port=0)
    try:
        with pytest.raises(RuntimeError, match="enable_server"):
            await endpoint.accept_next(timeout=0.1)
    finally:
        await endpoint.close()
