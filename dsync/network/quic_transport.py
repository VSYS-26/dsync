"""Run a QUIC connection over a caller-bound UDP socket.

The hole-punch path (PR 4) needs both peers to drive a QUIC handshake on a
specific UDP socket that has already been bound locally — typically the
same socket used for their relay control channel, so the NAT mapping the
relay observed is preserved. aioquic's high-level ``connect()`` / ``serve()``
helpers always open their own socket, so we go one level lower and wire a
``QuicConnection`` + ``QuicConnectionProtocol`` (dialer) or a ``QuicServer``
(listener) to the existing socket via ``loop.create_datagram_endpoint(sock=...)``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.asyncio.server import QuicServer
from aioquic.quic.connection import QuicConnection

if TYPE_CHECKING:
    import socket as _socket

    from aioquic.quic.configuration import QuicConfiguration


@dataclass
class DialerEndpoint:
    """Active QUIC connection plus the transport that backs it (caller closes)."""

    protocol: QuicConnectionProtocol
    transport: asyncio.DatagramTransport


@dataclass
class ListenerEndpoint:
    """Bound listener socket + a Future that fires on the first accepted peer.

    The endpoint is created once the UDP socket is bound and the QUIC server
    is wired in. The first peer connection that arrives resolves ``accepted``;
    caller awaits it to get the per-connection protocol object.
    """

    server: QuicServer
    transport: asyncio.DatagramTransport
    accepted: asyncio.Future[QuicConnectionProtocol]

    @property
    def protocol(self) -> QuicConnectionProtocol:
        """The first accepted protocol. Caller must await ``accepted`` first."""
        if not self.accepted.done():
            raise RuntimeError("listener has not accepted a connection yet")
        return self.accepted.result()

    async def wait_accepted(
        self,
        timeout: float | None = None,
    ) -> QuicConnectionProtocol:
        """Block until the first peer arrives. Returns the accepted protocol."""
        if timeout is None:
            return await self.accepted
        return await asyncio.wait_for(self.accepted, timeout=timeout)


async def start_dialer(
    *,
    sock: _socket.socket,
    peer_addr: tuple[str, int],
    configuration: QuicConfiguration,
) -> DialerEndpoint:
    """Begin a client-side QUIC handshake to ``peer_addr`` over ``sock``.

    The function returns once the handshake datagrams have been queued and
    the asyncio transport is wired up. Callers await
    ``endpoint.protocol.wait_connected()`` to block until the TLS handshake
    completes.

    Args:
        sock: A UDP socket the caller has already bound locally. ``sock``
            must not be in use by another asyncio transport.
        peer_addr: The listener's (host, port) — typically read from the
            relay's PUNCH_INFO.
        configuration: ``aioquic`` configuration; ``is_client`` must be
            ``True``. Caller is responsible for cert/key + ALPN.

    Returns:
        A ``DialerEndpoint`` carrying the running protocol and transport.
    """
    if not configuration.is_client:
        raise ValueError("start_dialer requires is_client=True")

    loop = asyncio.get_running_loop()
    quic_conn = QuicConnection(configuration=configuration)
    protocol = QuicConnectionProtocol(quic_conn)

    transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol,
        sock=sock,
    )

    quic_conn.connect(peer_addr, now=loop.time())
    protocol.transmit()

    return DialerEndpoint(protocol=protocol, transport=transport)


async def start_listener(
    *,
    sock: _socket.socket,
    configuration: QuicConfiguration,
    stream_handler: Any | None = None,
) -> ListenerEndpoint:
    """Bind a QUIC listener on the caller's socket; do not block on accept.

    Returns as soon as the UDP socket is registered with the event loop and
    the ``QuicServer`` is ready to dispatch incoming datagrams. The first
    peer connection that arrives resolves ``endpoint.accepted``; the caller
    awaits it (typically with a timeout) to drive the rest of the session.

    This non-blocking shape is what lets the test/runtime spawn the listener
    and the dialer in either order without a race window where the dialer's
    INITIAL is sent before the listener is bound.

    Args:
        sock: A locally-bound UDP socket.
        configuration: Server-side ``QuicConfiguration``; ``is_client``
            must be ``False``.
        stream_handler: Optional aioquic stream handler forwarded to each
            accepted connection.

    Returns:
        A ``ListenerEndpoint`` that is bound but has not yet accepted any
        peer. Use ``endpoint.wait_accepted(timeout=...)`` to block until a
        peer arrives.

    Raises:
        ValueError: If ``configuration.is_client`` is True.
    """
    if configuration.is_client:
        raise ValueError("start_listener requires is_client=False")

    loop = asyncio.get_running_loop()
    accepted: asyncio.Future[QuicConnectionProtocol] = loop.create_future()

    def _capturing_protocol(*args: Any, **kwargs: Any) -> QuicConnectionProtocol:
        proto = QuicConnectionProtocol(*args, **kwargs)
        if not accepted.done():
            accepted.set_result(proto)
        return proto

    server = QuicServer(
        configuration=configuration,
        create_protocol=_capturing_protocol,
        stream_handler=stream_handler,
    )

    transport, _ = await loop.create_datagram_endpoint(
        lambda: server,
        sock=sock,
    )

    return ListenerEndpoint(server=server, transport=transport, accepted=accepted)
