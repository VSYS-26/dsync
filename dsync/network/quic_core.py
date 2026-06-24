"""QUIC transport primitives: TLS configuration, channel binding, framing.

The relay-control channel, the peer-to-peer data channel and every helper
session in dsync ride on a QUIC stream and share the message-framing
primitives in this module: a 1-byte ``MsgType`` tag followed by a 4-byte
big-endian length and a body. The two channel-binding helpers
(``CHANNEL_BINDING_LABEL``, ``get_quic_channel_binding``) derive the
session-unique bytes both peers sign in their AUTH frame.
"""

from __future__ import annotations

import asyncio
from enum import IntEnum
import ssl
import struct
from typing import TYPE_CHECKING, Final, cast

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

#: Upper bound for a CONFIG frame payload (64 KiB is generous for YAML config).
MAX_CONFIG_SIZE: Final[int] = 64 * 1024

#: SPKI || sig payload size for the AUTH frame (RSA-2048 SPKI 294 B + sig 256 B).
_AUTH_PAYLOAD_SIZE: Final[int] = 550


class MsgType(IntEnum):
    """Logical message type on a QUIC stream (or relay-control channel).

    Values are stable across versions. Values 6+ are new and only appear on
    the peer-to-relay control channel.
    """

    # Peer-to-peer data channel (also AUTH on the relay control channel)
    AUTH = 0
    HELLO = 1
    FILE_META = 2
    FILE_CHUNK = 3
    CONFIG = 4
    CONFIG_ACK = 5
    FILE_VERIFY = 11
    FOLDER_ID = 12

    # Peer-to-relay control channel
    REGISTER_ACK = 6
    CONTROL_PING = 7
    CONNECT_REQUEST = 8
    PUNCH_INFO = 9
    ERROR = 10


async def async_send_msg(
    writer: asyncio.StreamWriter,
    msg_type: int,
    data: bytes,
) -> None:
    """Send a single framed message: ``[type:1][length:4 BE][body]``.

    Works on any byte-stream writer (asyncio TCP or aioquic stream).
    """
    header = struct.pack("!BI", msg_type, len(data))
    writer.write(header + data)
    await writer.drain()


async def async_recv_msg(
    reader: asyncio.StreamReader,
) -> tuple[int | None, bytes | None]:
    """Read a single framed message. Returns ``(None, None)`` on clean EOF."""
    try:
        header = await reader.readexactly(5)
    except asyncio.IncompleteReadError:
        return None, None

    msg_type, length = struct.unpack("!BI", header)
    try:
        data = await reader.readexactly(length)
    except asyncio.IncompleteReadError as err:
        raise RuntimeError("Connection lost during reception.") from err
    return msg_type, data


async def async_recv_auth_msg(reader: asyncio.StreamReader) -> bytes:
    """Read an AUTH frame and return the 550-byte SPKI||signature payload.

    Rejects wrong msg-type or size mismatch before any payload allocation.
    """
    try:
        header = await reader.readexactly(5)
    except asyncio.IncompleteReadError as err:
        raise RuntimeError("Connection closed before auth message received.") from err

    msg_type, length = struct.unpack("!BI", header)
    if msg_type != MsgType.AUTH:
        raise RuntimeError(f"Expected auth message (type {MsgType.AUTH}), got type {msg_type}")
    if length != _AUTH_PAYLOAD_SIZE:
        raise RuntimeError(
            f"Auth message wrong size: got {length} B, expected {_AUTH_PAYLOAD_SIZE} B"
        )
    try:
        return await reader.readexactly(_AUTH_PAYLOAD_SIZE)
    except asyncio.IncompleteReadError as err:
        raise RuntimeError("Connection lost during auth message reception.") from err


async def async_send_config(
    writer: asyncio.StreamWriter,
    config_data: bytes,
) -> None:
    """Send a CONFIG frame carrying serialized folder config (YAML bytes)."""
    await async_send_msg(writer, MsgType.CONFIG, config_data)


async def async_recv_config(reader: asyncio.StreamReader) -> bytes:
    """Read a CONFIG frame; reject if oversize or wrong type before reading body."""
    try:
        header = await reader.readexactly(5)
    except asyncio.IncompleteReadError as err:
        raise RuntimeError("Connection closed before config received") from err

    msg_type, length = struct.unpack("!BI", header)
    if msg_type != MsgType.CONFIG:
        raise RuntimeError(f"Expected CONFIG (type {MsgType.CONFIG}), got type {msg_type}")
    if length > MAX_CONFIG_SIZE:
        raise RuntimeError(
            f"Config payload too large: {length} B exceeds limit of {MAX_CONFIG_SIZE} B"
        )
    try:
        return await reader.readexactly(length)
    except asyncio.IncompleteReadError as err:
        raise RuntimeError("Connection lost during config reception") from err


async def async_send_config_ack(writer: asyncio.StreamWriter) -> None:
    """Send a zero-byte CONFIG_ACK frame."""
    await async_send_msg(writer, MsgType.CONFIG_ACK, b"")


async def async_recv_config_ack(reader: asyncio.StreamReader) -> None:
    """Read a CONFIG_ACK frame, raising if a different type arrives."""
    msg_type, _ = await async_recv_msg(reader)
    if msg_type is None:
        raise RuntimeError("Connection closed before config ack received")
    if msg_type != MsgType.CONFIG_ACK:
        raise RuntimeError(f"Expected CONFIG_ACK (type {MsgType.CONFIG_ACK}), got type {msg_type}")


def build_quic_configuration(
    *,
    is_client: bool,
    cert_path: Path | str,
    key_path: Path | str,
) -> QuicConfiguration:
    """Construct a QuicConfiguration with self-signed-cert defaults.

    Loads the local cert chain, disables peer-cert verification (CERT_NONE),
    and advertises the dsync ALPN. MITM resistance is provided by the
    application-layer AUTH frame, not by the X.509 PKI.

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
        raise RuntimeError("QUIC handshake has not completed yet; channel binding is unavailable")
    binding = hkdf_expand_label(
        algorithm=key_schedule.algorithm,
        secret=key_schedule.secret,
        label=CHANNEL_BINDING_LABEL,
        hash_value=key_schedule.hash.copy().finalize(),
        length=CHANNEL_BINDING_LENGTH,
    )
    return cast("bytes", binding)
