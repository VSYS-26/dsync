"""Peer-side relay client: registers with a relay and (PR 3) exits.

Long-running control-channel maintenance — keepalive, auto-reconnect, IPC
hand-off to ``sync run_backup`` — lands in PR 6 / PR 8. For now this module
provides the minimum needed to integration-test the relay server.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from aioquic.asyncio import connect
from aioquic.quic.connection import QuicConnection

from dsync.network.errors import RelayAuthError, RelayError, RelayProtocolError
from dsync.network.peer_auth import (
    extract_spki,
    fingerprint_from_spki,
    load_rsa_private_key,
    pack_auth_payload,
    sign_channel_binding,
)
from dsync.network.quic_core import (
    MsgType,
    build_quic_configuration,
    get_quic_channel_binding,
)
from dsync.network.relay_protocol import (
    RegisterAck,
    parse_error,
    parse_register_ack,
    recv_json,
    send_auth,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


async def register_to_relay(
    *,
    relay_host: str,
    relay_port: int,
    relay_fingerprint: str,
    cert_path: Path | str,
    key_path: Path | str,
) -> RegisterAck:
    """Connect to a relay, authenticate, and return the relay's REGISTER_ACK.

    Pure one-shot registration: opens a QUIC control connection, opens an AUTH
    stream, signs the channel binding with the local RSA key, sends the AUTH
    frame, reads the REGISTER_ACK on the same stream, and tears the
    connection down. The long-lived control loop (keepalive, hole-punch
    handling) is built on top of this primitive in later PRs.

    Args:
        relay_host: Relay hostname or IP.
        relay_port: Relay UDP port.
        relay_fingerprint: SHA-256 hex fingerprint pinned in relays.yaml.
            The relay's TLS cert SPKI must hash to this value.
        cert_path: Local PEM certificate path.
        key_path: Local PEM private key path.

    Returns:
        ``RegisterAck`` containing the relay's observed (NAT-translated)
        UDP endpoint for this peer.

    Raises:
        RelayAuthError: If the relay's pinned fingerprint does not match its
            TLS cert SPKI, or if the relay rejects the peer's AUTH.
        RelayProtocolError: If the relay sends a malformed frame.
        RelayError: For transport-level failures.
    """
    private_key = load_rsa_private_key(key_path)
    own_spki = extract_spki(private_key)
    own_fingerprint = fingerprint_from_spki(own_spki)

    config = build_quic_configuration(
        is_client=True,
        cert_path=cert_path,
        key_path=key_path,
    )

    try:
        async with connect(
            host=relay_host,
            port=relay_port,
            configuration=config,
        ) as protocol:
            quic_conn: QuicConnection = protocol._quic
            _verify_relay_cert(quic_conn, relay_fingerprint)

            reader, writer = await protocol.create_stream()
            binding = get_quic_channel_binding(quic_conn)
            signature = sign_channel_binding(private_key, binding)
            await send_auth(writer, pack_auth_payload(own_spki, signature))

            msg_type, body = await recv_json(reader)
            if msg_type == MsgType.ERROR:
                err = parse_error(body)
                raise RelayAuthError(f"relay rejected AUTH: {err.reason}")
            if msg_type != MsgType.REGISTER_ACK:
                raise RelayProtocolError(
                    f"Expected REGISTER_ACK (type {MsgType.REGISTER_ACK}), got {msg_type}"
                )
            ack = parse_register_ack(body)

            logger.info(
                "registered to relay %s as fp=%s; observed endpoint %s:%d",
                relay_fingerprint,
                own_fingerprint,
                ack.observed_host,
                ack.observed_port,
            )

            with contextlib.suppress(Exception):
                writer.close()
            return ack
    except (RelayAuthError, RelayProtocolError):
        raise
    except Exception as exc:
        raise RelayError(f"could not register with relay: {exc}") from exc


def _verify_relay_cert(connection: QuicConnection, expected_fingerprint: str) -> None:
    """Compare the relay's TLS-presented SPKI fingerprint to ``expected_fingerprint``.

    The relay's identity is pinned via ``relays.yaml``: the peer refuses to
    talk to any relay whose certificate's SubjectPublicKeyInfo does not hash
    to the configured value. This is the relay-trust check; the relay does
    not need its own AUTH frame.
    """
    peer_cert = getattr(connection.tls, "_peer_certificate", None)
    if peer_cert is None:
        raise RelayAuthError("relay did not present a TLS certificate")

    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    spki = peer_cert.public_key().public_bytes(
        Encoding.DER,
        PublicFormat.SubjectPublicKeyInfo,
    )
    actual_fingerprint = fingerprint_from_spki(spki)
    if actual_fingerprint != expected_fingerprint:
        raise RelayAuthError(
            f"relay fingerprint mismatch: expected {expected_fingerprint}, "
            f"got {actual_fingerprint}"
        )
