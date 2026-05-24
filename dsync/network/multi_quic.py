"""Multi-connection QUIC over a single UDP socket.

Hosting both the relay control channel and one or more peer-to-peer data
channels on the **same** UDP socket is what lets the NAT mapping the relay
observes survive into the hole-punch phase: same kernel socket → same
external (NAT-translated) ``(ip, port)``. This module is what makes that
possible.

``MultiQuicEndpoint`` is an ``asyncio.DatagramProtocol`` that owns the bound
socket and routes incoming datagrams to one of several
``QuicConnectionProtocol`` instances by source address. All children share
the same ``DatagramTransport`` — aioquic's
``QuicConnectionProtocol.transmit()`` calls ``sendto(data, addr)`` with the
peer addr taken from its own ``QuicConnection``, so multiple protocols
happily coexist on one transport.

Two ways a child connection is created:

* :meth:`add_outgoing` — caller knows the peer addr (relay or hole-punched
  counterparty). We build a client ``QuicConnection``, register it under
  ``peer_addr`` and fire the QUIC INITIAL.
* Server-style accept — :meth:`enable_server` lets unknown source addrs
  trigger a server-side ``QuicConnection``, modelled after the small slice
  of ``aioquic.asyncio.server.QuicServer`` we actually need.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, cast

from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.buffer import Buffer
from aioquic.quic.connection import QuicConnection
from aioquic.quic.packet import (
    QuicPacketType,
    is_long_header,
    pull_quic_header,
)

if TYPE_CHECKING:
    import socket as _socket

    from aioquic.quic.configuration import QuicConfiguration

logger = logging.getLogger(__name__)


class MultiQuicEndpoint(asyncio.DatagramProtocol):
    """Multi-connection QUIC endpoint over one bound UDP socket.

    Lifecycle::

        endpoint = await MultiQuicEndpoint.from_socket(sock)
        endpoint.enable_server(server_cfg, stream_handler=on_stream)
        protocol_to_relay = await endpoint.add_outgoing(relay_addr, client_cfg)
        # ... later, after PUNCH_INFO arrives:
        protocol_to_peer = await endpoint.add_outgoing(peer_addr, client_cfg2)
        # ... or, if the peer dialed us first, on_stream is invoked by the
        # accepted protocol and we can grab it from endpoint.accept_next()
        await endpoint.close()
    """

    def __init__(self) -> None:
        self._transport: asyncio.DatagramTransport | None = None
        self._protocols_by_addr: dict[
            tuple[str, int], QuicConnectionProtocol
        ] = {}
        self._server_config: QuicConfiguration | None = None
        self._server_stream_handler: Any | None = None
        self._server_factory: Any | None = None
        # Queue of accepted (server-side) protocols that nobody has consumed yet.
        self._accept_queue: asyncio.Queue[QuicConnectionProtocol] = asyncio.Queue()

    # ---- asyncio.DatagramProtocol interface -----------------------------------

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Cache the transport handed to us by ``create_datagram_endpoint``."""
        self._transport = cast("asyncio.DatagramTransport", transport)

    def connection_lost(self, exc: Exception | None) -> None:
        """Tear down any child connections when the underlying socket dies."""
        for proto in list(self._protocols_by_addr.values()):
            try:
                proto.connection_lost(exc)
            except Exception:
                logger.exception("child protocol connection_lost raised")
        self._protocols_by_addr.clear()

    def error_received(self, exc: Exception) -> None:
        """ICMP / unreachable etc. — log and ignore so the socket stays open."""
        logger.debug("DatagramProtocol error_received: %s", exc)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Dispatch ``data`` to the right child protocol (or accept a new one)."""
        proto = self._protocols_by_addr.get(addr)
        if proto is None and self._server_config is not None:
            proto = self._maybe_accept(data, addr)
        if proto is None:
            return  # unknown peer, server mode off → drop
        proto.datagram_received(data, addr)

    # ---- public API ----------------------------------------------------------

    @classmethod
    async def from_socket(cls, sock: _socket.socket) -> MultiQuicEndpoint:
        """Build an endpoint over an already-bound UDP socket."""
        endpoint = cls()
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(lambda: endpoint, sock=sock)
        return endpoint

    @classmethod
    async def bind(
        cls, *, host: str = "0.0.0.0", port: int = 0
    ) -> MultiQuicEndpoint:
        """Bind a fresh UDP socket on ``host:port`` and return an endpoint."""
        endpoint = cls()
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(
            lambda: endpoint, local_addr=(host, port)
        )
        return endpoint

    @property
    def local_addr(self) -> tuple[str, int]:
        """The bound ``(host, port)`` of the underlying UDP socket."""
        if self._transport is None:
            raise RuntimeError("endpoint not yet connected")
        sockname = self._transport.get_extra_info("sockname")
        return cast("tuple[str, int]", sockname)

    def enable_server(
        self,
        configuration: QuicConfiguration,
        *,
        stream_handler: Any | None = None,
    ) -> None:
        """Allow unknown source addrs to trigger server-side connections.

        Newly accepted connections are placed on an internal queue; the
        caller pulls them out with :meth:`accept_next`.

        Args:
            configuration: Server-side ``QuicConfiguration`` (``is_client=False``).
            stream_handler: Optional aioquic stream handler forwarded to
                each accepted connection.
        """
        if configuration.is_client:
            raise ValueError("enable_server requires is_client=False")
        self._server_config = configuration
        self._server_stream_handler = stream_handler

    async def accept_next(
        self, timeout: float | None = None
    ) -> QuicConnectionProtocol:
        """Block until the next server-side connection is accepted.

        Args:
            timeout: Seconds to wait; ``None`` waits forever.

        Returns:
            The newly accepted ``QuicConnectionProtocol``.

        Raises:
            TimeoutError: If no connection arrives within ``timeout``.
        """
        if self._server_config is None:
            raise RuntimeError("enable_server() must be called first")
        if timeout is None:
            return await self._accept_queue.get()
        return await asyncio.wait_for(self._accept_queue.get(), timeout=timeout)

    async def add_outgoing(
        self,
        peer_addr: tuple[str, int],
        configuration: QuicConfiguration,
        *,
        stream_handler: Any | None = None,
    ) -> QuicConnectionProtocol:
        """Add a client-side QUIC connection bound to ``peer_addr``.

        The returned protocol is wired to share this endpoint's transport,
        so its outgoing datagrams travel through the same UDP socket and
        therefore the same NAT mapping as every other child on this endpoint.

        Args:
            peer_addr: Destination ``(host, port)``.
            configuration: Client-side ``QuicConfiguration`` (``is_client=True``).
            stream_handler: Optional aioquic stream handler.

        Returns:
            The connected protocol. Await ``protocol.wait_connected()`` to
            block until the TLS handshake completes.

        Raises:
            ValueError: If ``configuration.is_client`` is ``False`` or if
                ``peer_addr`` already has a registered connection.
        """
        if not configuration.is_client:
            raise ValueError("add_outgoing requires is_client=True")
        if peer_addr in self._protocols_by_addr:
            raise ValueError(f"peer addr {peer_addr} already attached")
        if self._transport is None:
            raise RuntimeError("endpoint not yet connected")

        quic_conn = QuicConnection(configuration=configuration)
        protocol = QuicConnectionProtocol(quic_conn, stream_handler=stream_handler)
        protocol.connection_made(self._transport)
        self._protocols_by_addr[peer_addr] = protocol

        quic_conn.connect(peer_addr, now=asyncio.get_running_loop().time())
        protocol.transmit()
        return protocol

    def remove_connection(self, peer_addr: tuple[str, int]) -> None:
        """Drop ``peer_addr`` from the routing table (does not close the protocol)."""
        self._protocols_by_addr.pop(peer_addr, None)

    def send_datagram(self, data: bytes, peer_addr: tuple[str, int]) -> None:
        """Send a raw UDP datagram from the multiplexed socket to ``peer_addr``.

        Bypasses every child ``QuicConnection``: useful for the hole-punch
        burst, which needs to leave from the *same* external NAT port the
        relay observed. The datagram is not a valid QUIC packet, so any
        aioquic stack receiving it silently drops it.
        """
        if self._transport is None:
            raise RuntimeError("endpoint not yet connected")
        self._transport.sendto(data, peer_addr)

    async def close(self) -> None:
        """Close every child protocol and the underlying transport."""
        for proto in list(self._protocols_by_addr.values()):
            try:
                proto.close()
            except Exception:
                logger.exception("child protocol close raised")
        self._protocols_by_addr.clear()
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    # ---- internals -----------------------------------------------------------

    def _maybe_accept(
        self, data: bytes, addr: tuple[str, int]
    ) -> QuicConnectionProtocol | None:
        """Try to spawn a server-side QUIC connection for ``addr``.

        Only long-header INITIAL packets create new connections. Short-header
        traffic from an unknown addr is dropped — it can only belong to an
        existing connection we don't have.
        """
        if not data:
            return None
        if not is_long_header(data[0]):
            return None
        try:
            header = pull_quic_header(Buffer(data=data), host_cid_length=8)
        except ValueError:
            return None
        if header.packet_type != QuicPacketType.INITIAL:
            return None

        assert self._server_config is not None  # checked by datagram_received

        quic_conn = QuicConnection(
            configuration=self._server_config,
            original_destination_connection_id=header.destination_cid,
        )
        protocol = QuicConnectionProtocol(
            quic_conn,
            stream_handler=self._server_stream_handler,
        )
        assert self._transport is not None
        protocol.connection_made(self._transport)
        self._protocols_by_addr[addr] = protocol

        try:
            self._accept_queue.put_nowait(protocol)
        except asyncio.QueueFull:
            logger.warning("accept queue full; dropping protocol from %s", addr)
            self._protocols_by_addr.pop(addr, None)
            return None

        return protocol
