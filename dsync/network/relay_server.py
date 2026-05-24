"""Pure-rendezvous relay server.

A relay maintains long-lived QUIC control connections with each registered
peer, observes their NATted UDP endpoint, and brokers hole-punching when
two peers want to sync. **File bytes never traverse the relay.**

The protocol on this control channel is defined in ``relay_protocol``.
This module is purely server-side: peer-side wiring lives in
``relay_client``. Both share the wire protocol and the auth helpers in
``peer_auth``.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING, Any, cast

from aioquic.asyncio import serve
from aioquic.asyncio.protocol import QuicConnectionProtocol

from dsync.network.errors import RelayAuthError, RelayError, RelayProtocolError
from dsync.network.peer_auth import (
    extract_spki,
    fingerprint_from_spki,
    load_rsa_private_key,
    unpack_auth_payload,
    verify_signature,
)
from dsync.network.quic_core import (
    MsgType,
    build_quic_configuration,
    get_quic_channel_binding,
)
from dsync.network.relay_protocol import (
    ErrorMessage,
    PunchInfo,
    RegisterAck,
    parse_connect_request,
    send_json,
)

if TYPE_CHECKING:
    from pathlib import Path

    from aioquic.asyncio.server import QuicServer
    from aioquic.quic.connection import QuicConnection

logger = logging.getLogger(__name__)


@dataclass
class _PeerRegistration:
    """One registered peer: identity + control connection + observed endpoint."""

    fingerprint: str
    observed_host: str
    observed_port: int
    protocol: QuicConnectionProtocol = field(repr=False)


class _RelayProtocol(QuicConnectionProtocol):
    """QuicConnectionProtocol subclass that knows how to clean up the registry.

    aioquic creates one instance per inbound QUIC connection. We register the
    instance into ``RelayServer`` when AUTH succeeds and remove it on
    ``connection_lost`` so stale entries cannot leak.
    """

    def __init__(self, *args: Any, server: RelayServer, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._server = server
        self._registered_fingerprint: str | None = None

    def connection_lost(self, exc: Exception | None) -> None:
        """Drop this peer from the registry as soon as its QUIC channel dies."""
        if self._registered_fingerprint is not None:
            self._server._deregister(self._registered_fingerprint, self)
        super().connection_lost(exc)


class RelayServer:
    """Pure-rendezvous relay running on QUIC.

    Lifecycle::

        relay = RelayServer(host="0.0.0.0", port=9000, cert_path=..., key_path=...)
        await relay.start()           # binds the UDP socket, returns immediately
        # ... do other stuff ...
        await relay.close()

    The relay holds a registry of ``{fingerprint -> _PeerRegistration}``. A new
    peer registers itself by opening a stream and sending an AUTH frame; the
    relay verifies the signature against the QUIC channel binding and replies
    with ``REGISTER_ACK`` containing the peer's observed (NAT-translated) UDP
    endpoint.

    Hole-punch brokering (``CONNECT_REQUEST`` → ``PUNCH_INFO``) is implemented
    here in skeleton form; PR 4 wires it into the actual UDP punch and PR 5
    plugs in the resulting peer-to-peer QUIC session.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        cert_path: Path | str,
        key_path: Path | str,
    ) -> None:
        """Build a relay bound to ``host:port`` using the given TLS cert pair."""
        self._host = host
        self._port = port
        self._cert_path = cert_path
        self._key_path = key_path
        self._private_key = load_rsa_private_key(key_path)
        self._own_spki = extract_spki(self._private_key)
        self._own_fingerprint = fingerprint_from_spki(self._own_spki)

        self._registry: dict[str, _PeerRegistration] = {}
        self._registry_lock = asyncio.Lock()
        self._server: QuicServer | None = None
        self._stream_tasks: set[asyncio.Task[None]] = set()

    @property
    def fingerprint(self) -> str:
        """SHA-256 hex fingerprint of this relay's own public key."""
        return self._own_fingerprint

    @property
    def bound_port(self) -> int:
        """The actual UDP port the relay is listening on (resolves ``port=0``)."""
        if self._server is None:
            raise RelayError("Relay has not been started yet")
        sockname = self._server._transport.get_extra_info("sockname")
        return cast("int", sockname[1])

    async def registered_fingerprints(self) -> list[str]:
        """Snapshot of currently registered peer fingerprints (for tests/CLI)."""
        async with self._registry_lock:
            return list(self._registry.keys())

    async def start(self) -> None:
        """Bind the UDP socket and begin accepting peer connections."""
        config = build_quic_configuration(
            is_client=False,
            cert_path=self._cert_path,
            key_path=self._key_path,
        )

        def _factory(*args: Any, **kwargs: Any) -> _RelayProtocol:
            return _RelayProtocol(*args, server=self, **kwargs)

        self._server = await serve(
            host=self._host,
            port=self._port,
            configuration=config,
            create_protocol=_factory,
            stream_handler=self._on_stream_opened,
        )

    async def close(self) -> None:
        """Stop the relay and close all peer connections."""
        if self._server is not None:
            self._server.close()
            self._server = None
        async with self._registry_lock:
            self._registry.clear()

    # ------------------------------------------------------------------ private

    def _on_stream_opened(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Aioquic invokes this synchronously; spawn an async handler task.

        The reference is kept on ``self._stream_tasks`` so a quickly-finishing
        coroutine cannot be garbage-collected mid-flight.
        """
        task = asyncio.create_task(self._run_stream(reader, writer))
        self._stream_tasks.add(task)
        task.add_done_callback(self._stream_tasks.discard)

    async def _run_stream(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            protocol = _protocol_from_writer(writer)
            type_byte = await reader.readexactly(1)
            msg_type = MsgType(type_byte[0])
            if msg_type == MsgType.AUTH:
                await self._handle_auth(reader, writer, protocol)
            elif msg_type == MsgType.CONNECT_REQUEST:
                await self._handle_connect_request(reader, writer, protocol)
            elif msg_type == MsgType.CONTROL_PING:
                # Drain the body, then reply with an empty PING.
                await reader.read()
                await send_json(writer, MsgType.CONTROL_PING, None)
            else:
                raise RelayProtocolError(
                    f"Unexpected initial message type on relay stream: {msg_type}"
                )
        except (RelayProtocolError, RelayAuthError, ValueError) as exc:
            logger.warning("relay stream rejected: %s", exc)
            with contextlib.suppress(Exception):
                await send_json(writer, MsgType.ERROR, ErrorMessage(reason=str(exc)))
        except Exception:
            logger.exception("relay stream crashed")

    async def _handle_auth(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        protocol: QuicConnectionProtocol,
    ) -> None:
        """Read AUTH body, validate, register, reply REGISTER_ACK on same stream.

        The leading MsgType byte has already been consumed by ``_run_stream``,
        so we only read the 550-byte SPKI||signature payload here.
        """
        from dsync.network.peer_auth import AUTH_PAYLOAD_SIZE

        body = await reader.readexactly(AUTH_PAYLOAD_SIZE)
        trailing = await reader.read()
        if trailing:
            raise RelayProtocolError(
                f"AUTH stream had {len(trailing)} unexpected trailing bytes"
            )

        spki, sig = unpack_auth_payload(body)
        binding = get_quic_channel_binding(protocol._quic)
        try:
            verify_signature(spki, binding, sig)
        except ValueError as exc:
            raise RelayAuthError(str(exc)) from exc

        fingerprint = fingerprint_from_spki(spki)
        observed_host, observed_port = _peer_addr(protocol._quic)

        async with self._registry_lock:
            self._registry[fingerprint] = _PeerRegistration(
                fingerprint=fingerprint,
                observed_host=observed_host,
                observed_port=observed_port,
                protocol=protocol,
            )

        # Bind the protocol → fingerprint so connection_lost can clean up.
        if isinstance(protocol, _RelayProtocol):
            protocol._registered_fingerprint = fingerprint

        logger.info(
            "peer registered: fp=%s observed=%s:%d",
            fingerprint,
            observed_host,
            observed_port,
        )

        await send_json(
            writer,
            MsgType.REGISTER_ACK,
            RegisterAck(observed_host=observed_host, observed_port=observed_port),
        )

    async def _handle_connect_request(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        protocol: QuicConnectionProtocol,
    ) -> None:
        body = await reader.read()
        request = parse_connect_request(body)
        # Identify the requesting peer by reverse-looking-up the protocol.
        source = await self._registration_for_protocol(protocol)
        if source is None:
            raise RelayAuthError("CONNECT_REQUEST before AUTH")

        async with self._registry_lock:
            target = self._registry.get(request.target_fingerprint)

        if target is None:
            await send_json(
                writer,
                MsgType.ERROR,
                ErrorMessage(reason=f"target peer {request.target_fingerprint} is not registered"),
            )
            return

        # Tell the dialer where to punch. The actual hole-punch coordination
        # is implemented in PR 4; for PR 3 we just deliver PUNCH_INFO.
        await send_json(
            writer,
            MsgType.PUNCH_INFO,
            PunchInfo(
                peer_host=target.observed_host,
                peer_port=target.observed_port,
                peer_fingerprint=target.fingerprint,
                role="dialer",
            ),
        )

        # And push the same PUNCH_INFO to the listener on a fresh stream.
        listener_reader, listener_writer = await target.protocol.create_stream()
        del listener_reader
        await send_json(
            listener_writer,
            MsgType.PUNCH_INFO,
            PunchInfo(
                peer_host=source.observed_host,
                peer_port=source.observed_port,
                peer_fingerprint=source.fingerprint,
                role="listener",
            ),
        )

    async def _registration_for_protocol(
        self,
        protocol: QuicConnectionProtocol,
    ) -> _PeerRegistration | None:
        async with self._registry_lock:
            for reg in self._registry.values():
                if reg.protocol is protocol:
                    return reg
        return None

    def _deregister(
        self,
        fingerprint: str,
        protocol: QuicConnectionProtocol,
    ) -> None:
        """Synchronous removal hook invoked from ``connection_lost``.

        We avoid taking the asyncio lock here because ``connection_lost`` is a
        synchronous callback. The dict assignment is atomic and the worst-case
        race (a CONNECT_REQUEST seeing a stale entry) is harmless: PUNCH_INFO
        will just point at a peer that has already vanished and the punch
        will fail loudly.
        """
        existing = self._registry.get(fingerprint)
        if existing is not None and existing.protocol is protocol:
            self._registry.pop(fingerprint, None)


def _protocol_from_writer(writer: asyncio.StreamWriter) -> QuicConnectionProtocol:
    """Reach through aioquic's stream adapter to the owning protocol."""
    adapter = writer.transport
    protocol = getattr(adapter, "protocol", None)
    if not isinstance(protocol, QuicConnectionProtocol):
        raise RelayProtocolError("writer is not backed by a QuicConnectionProtocol")
    return protocol


def _peer_addr(connection: QuicConnection) -> tuple[str, int]:
    """Pull the peer's most recently observed UDP endpoint from the connection."""
    paths = getattr(connection, "_network_paths", None)
    if not paths:
        raise RelayProtocolError("QuicConnection has no validated network path yet")
    addr = paths[0].addr
    return cast("tuple[str, int]", addr)
