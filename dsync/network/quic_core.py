"""QUIC transport primitives: TLS configuration, framing types, channel binding.

Replaces the TCP+TLS scaffolding in ``p2p_core.py`` for the upcoming
relay+P2P architecture. Kept side-by-side with ``p2p_core.py`` until the
legacy direct-connection code is removed (PR 7).
"""

from __future__ import annotations

from enum import IntEnum
import ssl
from typing import TYPE_CHECKING, cast

from aioquic.quic.configuration import QuicConfiguration
from aioquic.tls import hkdf_expand_label

if TYPE_CHECKING:
    from pathlib import Path

    from aioquic.quic.connection import QuicConnection

#: ALPN identifier advertised by both relay-control and peer-data QUIC connections.
ALPN_PROTOCOL = "dsync/1"

#: Label fed into the TLS-1.3 exporter-style derivation (RFC 8446 §7.5 style).
#: The value is namespaced to dsync so it cannot collide with other exporters
#: derived from the same TLS session.
CHANNEL_BINDING_LABEL = b"dsync-peer-auth-v1"

#: Length of the channel-binding output in bytes. Both peers sign this value
#: with their private key to prove they participate in this specific session.
CHANNEL_BINDING_LENGTH = 32


class MsgType(IntEnum):
    """Logical message type on a QUIC stream (or relay-control channel).

    Values 0-5 are carried over from the legacy TCP framing in ``p2p_core.py``
    so the AUTH/HELLO/FILE/CONFIG semantics stay numerically stable across
    the transport swap. Values 6+ are new and only appear on the
    peer-to-relay control channel.
    """

    # Peer-to-peer data channel (also AUTH on the relay control channel)
    AUTH = 0
    HELLO = 1
    FILE_META = 2
    FILE_CHUNK = 3
    CONFIG = 4
    CONFIG_ACK = 5

    # Peer-to-relay control channel
    REGISTER_ACK = 6
    CONTROL_PING = 7
    CONNECT_REQUEST = 8
    PUNCH_INFO = 9
    ERROR = 10


def build_quic_configuration(
    *,
    is_client: bool,
    cert_path: Path | str,
    key_path: Path | str,
) -> QuicConfiguration:
    """Construct a QuicConfiguration with self-signed-cert defaults.

    Mirrors the TLS posture of ``p2p_core.create_tls_context``: load the local
    cert chain, disable peer-cert verification (CERT_NONE), advertise the
    dsync ALPN. MITM resistance is provided by the application-layer AUTH
    frame, not by the X.509 PKI.

    Args:
        is_client: True for the dialer, False for the listener.
        cert_path: Path to the local certificate (PEM).
        key_path: Path to the local private key (PEM).

    Returns:
        A QuicConfiguration ready to be passed to ``aioquic.asyncio.connect``
        or ``aioquic.asyncio.serve``.
    """
    config = QuicConfiguration(
        is_client=is_client,
        alpn_protocols=[ALPN_PROTOCOL],
        verify_mode=ssl.CERT_NONE,
    )
    config.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return config


def get_quic_channel_binding(connection: QuicConnection) -> bytes:
    """Derive ``CHANNEL_BINDING_LENGTH`` bytes of session-unique TLS material.

    Both peers in a completed QUIC handshake compute the same value when they
    apply this function: the underlying KeySchedule secret and transcript
    hash converge once the handshake has finished. The output is suitable as
    the message signed by the peer's RSA key in the AUTH frame (replacing
    the RFC 5705 ``tls-exporter`` path from the legacy TCP code).

    aioquic does not expose a public TLS-exporter API. We reach into
    ``connection.tls.key_schedule`` and re-run aioquic's own HKDF-Expand-Label
    primitive, which is what every TLS-1.3 exporter is built from.

    Args:
        connection: A ``QuicConnection`` whose handshake has completed.

    Returns:
        Exactly ``CHANNEL_BINDING_LENGTH`` bytes derived from this session.

    Raises:
        RuntimeError: If the handshake has not produced a KeySchedule yet.
    """
    tls = getattr(connection, "tls", None)
    key_schedule = getattr(tls, "key_schedule", None) if tls is not None else None
    if key_schedule is None:
        raise RuntimeError(
            "QUIC handshake has not completed yet; channel binding is unavailable"
        )
    binding = hkdf_expand_label(
        algorithm=key_schedule.algorithm,
        secret=key_schedule.secret,
        label=CHANNEL_BINDING_LABEL,
        hash_value=key_schedule.hash.copy().finalize(),
        length=CHANNEL_BINDING_LENGTH,
    )
    return cast("bytes", binding)
