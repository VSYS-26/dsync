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
    """First accepted QUIC connection on the bound socket, plus the QuicServer host."""

    protocol: QuicConnectionProtocol
    server: QuicServer
    transport: asyncio.DatagramTransport


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
    accept_timeout: float = 10.0,
    stream_handler: Any | None = None,
) -> ListenerEndpoint:
    """Accept exactly one incoming QUIC connection on the bound socket.

    Internally hosts an ``aioquic.asyncio.server.QuicServer`` over the
    caller-provided socket and resolves as soon as the first peer
    connection appears. Subsequent peer connections to the same socket
    will be dispatched but ignored by this helper — that scenario only
    arises if the socket is later multiplexed with a relay-control
    connection (PR 6+).

    Args:
        sock: A locally-bound UDP socket.
        configuration: Server-side ``QuicConfiguration``; ``is_client``
            must be ``False``.
        accept_timeout: Seconds to wait for the first dialer to appear.
        stream_handler: Optional aioquic stream handler forwarded to
            each accepted connection.

    Returns:
        A ``ListenerEndpoint`` with the first accepted protocol.

    Raises:
        TimeoutError: If no peer connects within ``accept_timeout`` seconds.
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

    try:
        protocol = await asyncio.wait_for(accepted, timeout=accept_timeout)
    except TimeoutError:
        transport.close()
        raise

    return ListenerEndpoint(protocol=protocol, server=server, transport=transport)
