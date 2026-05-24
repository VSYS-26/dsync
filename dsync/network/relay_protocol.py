"""Wire protocol for the peer ↔ relay control channel over QUIC.

Each logical message is sent on its own QUIC stream as
``[MsgType byte][body bytes]`` and terminated with ``write_eof()`` (QUIC
end_stream). Bidirectional streams carry request/response pairs: the side
that opened the stream writes its message and EOFs; the other side reads
to EOF, then writes its reply on the same stream and EOFs in turn.

Frame layout::

    AUTH  : [MsgType.AUTH (1 byte)] [SPKI (294 bytes)] [signature (256 bytes)]
            Total: 551 bytes. Size is fixed, so no length prefix is needed.

    Other : [MsgType (1 byte)] [UTF-8 JSON body bytes]
            Body length is implied by the QUIC end_stream marker. The relay
            enforces ``MAX_CONTROL_BODY_SIZE`` to bound the payload.

The relay's own identity is pinned via its TLS certificate's SPKI
fingerprint (compared against ``relays.yaml``) — there is no relay-side
AUTH frame.
"""

from __future__ import annotations

import asyncio
import json
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from dsync.network.errors import RelayProtocolError
from dsync.network.peer_auth import AUTH_PAYLOAD_SIZE
from dsync.network.quic_core import MsgType

#: Hard cap on a JSON-encoded control message body. Generous: even a 64-char
#: fingerprint + endpoint info fits comfortably under a kilobyte.
MAX_CONTROL_BODY_SIZE: Final[int] = 4 * 1024


# ---------------------------------------------------------------------------
# Message models
# ---------------------------------------------------------------------------


class RegisterAck(BaseModel):
    """Relay's reply to a successful AUTH: the peer's observed UDP endpoint.

    Both ``observed_host`` and ``observed_port`` are what the relay actually
    saw on the wire (after NAT). The peer signs and broadcasts these back to
    its sync partner during the hole-punch in PR 4.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    observed_host: str = Field(min_length=1)
    observed_port: int = Field(ge=1, le=65535)


class ConnectRequest(BaseModel):
    """Peer → relay: please broker a hole-punch to this target fingerprint."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    target_fingerprint: str = Field(min_length=1)


class PunchInfo(BaseModel):
    """Relay → peer: the matched counterparty's endpoint and role assignment."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    peer_host: str = Field(min_length=1)
    peer_port: int = Field(ge=1, le=65535)
    peer_fingerprint: str = Field(min_length=1)
    role: Literal["dialer", "listener"]


class ErrorMessage(BaseModel):
    """Relay → peer: signals a protocol-level failure on a request stream."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    reason: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Stream framing
# ---------------------------------------------------------------------------


async def send_auth(writer: asyncio.StreamWriter, payload: bytes) -> None:
    """Write an AUTH frame (1 type byte + 550-byte payload) and EOF the stream."""
    if len(payload) != AUTH_PAYLOAD_SIZE:
        raise RelayProtocolError(
            f"AUTH payload must be {AUTH_PAYLOAD_SIZE} B, got {len(payload)}"
        )
    writer.write(bytes([MsgType.AUTH]) + payload)
    writer.write_eof()
    await writer.drain()


async def recv_auth(reader: asyncio.StreamReader) -> bytes:
    """Read an AUTH frame and return the raw SPKI||sig payload."""
    type_byte = await reader.readexactly(1)
    if type_byte[0] != MsgType.AUTH:
        raise RelayProtocolError(
            f"Expected AUTH (type {MsgType.AUTH}), got type {type_byte[0]}"
        )
    payload = await reader.readexactly(AUTH_PAYLOAD_SIZE)
    trailing = await reader.read()
    if trailing:
        raise RelayProtocolError(
            f"AUTH stream had {len(trailing)} unexpected trailing bytes"
        )
    return payload


async def send_json(
    writer: asyncio.StreamWriter,
    msg_type: MsgType,
    model: BaseModel | None = None,
) -> None:
    """Write a control message as ``[MsgType][utf-8 JSON?]`` and EOF the stream.

    Args:
        writer: QUIC stream writer.
        msg_type: The message-type tag (must not be ``AUTH``).
        model: Pydantic model to serialise; pass ``None`` for empty bodies
            like ``CONTROL_PING``.
    """
    if msg_type == MsgType.AUTH:
        raise RelayProtocolError("send_json must not be used for AUTH frames")
    body = model.model_dump_json().encode("utf-8") if model is not None else b""
    if len(body) > MAX_CONTROL_BODY_SIZE:
        raise RelayProtocolError(
            f"Control body too large: {len(body)} B exceeds {MAX_CONTROL_BODY_SIZE}"
        )
    writer.write(bytes([msg_type]) + body)
    writer.write_eof()
    await writer.drain()


async def recv_json(reader: asyncio.StreamReader) -> tuple[MsgType, bytes]:
    """Read one full JSON-or-empty control frame.

    Returns:
        ``(msg_type, body_bytes)`` where ``body_bytes`` is the UTF-8 JSON
        payload (or empty for ``CONTROL_PING``).

    Raises:
        RelayProtocolError: If the message type byte is ``AUTH`` (caller
            should have used :func:`recv_auth`), unknown, or the body
            exceeds ``MAX_CONTROL_BODY_SIZE``.
    """
    type_byte = await reader.readexactly(1)
    try:
        msg_type = MsgType(type_byte[0])
    except ValueError as exc:
        raise RelayProtocolError(f"Unknown message type {type_byte[0]}") from exc
    if msg_type == MsgType.AUTH:
        raise RelayProtocolError("recv_json was passed an AUTH stream")
    body = await reader.read()
    if len(body) > MAX_CONTROL_BODY_SIZE:
        raise RelayProtocolError(
            f"Control body too large: {len(body)} B exceeds {MAX_CONTROL_BODY_SIZE}"
        )
    return msg_type, body


# ---------------------------------------------------------------------------
# Body (de)serialisation helpers
# ---------------------------------------------------------------------------


def parse_register_ack(body: bytes) -> RegisterAck:
    """Validate and parse a REGISTER_ACK body."""
    return _parse(RegisterAck, body)


def parse_connect_request(body: bytes) -> ConnectRequest:
    """Validate and parse a CONNECT_REQUEST body."""
    return _parse(ConnectRequest, body)


def parse_punch_info(body: bytes) -> PunchInfo:
    """Validate and parse a PUNCH_INFO body."""
    return _parse(PunchInfo, body)


def parse_error(body: bytes) -> ErrorMessage:
    """Validate and parse an ERROR body."""
    return _parse(ErrorMessage, body)


def _parse[T: BaseModel](model_cls: type[T], body: bytes) -> T:
    try:
        return model_cls.model_validate(json.loads(body.decode("utf-8")))
    except Exception as exc:
        raise RelayProtocolError(
            f"Malformed {model_cls.__name__} payload: {exc}"
        ) from exc
