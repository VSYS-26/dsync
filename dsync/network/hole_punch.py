"""UDP hole-punch coordinator: burst then QUIC handshake, retry once.

The relay tells each peer (a) its counterparty's NAT-observed UDP endpoint
and (b) whether to act as the dialer or the listener. From there both
peers run :func:`do_hole_punch` concurrently. Each side:

1. Sends a short burst of small UDP datagrams toward the other peer's
   endpoint. Each datagram opens (or refreshes) the local NAT's outbound
   mapping for that ``(local_socket, peer_addr)`` 5-tuple, so the
   counterparty's QUIC INITIAL is allowed back in.
2. Starts its half of the QUIC handshake on the same socket: the dialer
   sends INITIAL, the listener waits for one.
3. Awaits handshake completion. On timeout the dialer rebuilds its
   ``QuicConnection`` and tries again (one retry).

The punch payload is a fixed magic byte sequence that does not parse as a
QUIC header, so any aioquic stack receiving it on the same socket drops
it harmlessly.
"""

from __future__ import annotations

import asyncio
import logging
import socket as _socket_mod
from typing import TYPE_CHECKING, Final, Literal

from dsync.network.errors import RelayError
from dsync.network.quic_transport import (
    DialerEndpoint,
    ListenerEndpoint,
    start_dialer,
    start_listener,
)

if TYPE_CHECKING:
    import socket as _socket

    from aioquic.quic.configuration import QuicConfiguration

    from dsync.network.multi_quic import MultiQuicEndpoint

logger = logging.getLogger(__name__)

#: Magic bytes for a hole-punch UDP packet. Chosen so the first byte (0x44
#: = 'D') is **not** a valid QUIC long-header form (high bit must be 1 for
#: long header), so aioquic silently drops it.
PUNCH_MAGIC: Final[bytes] = b"DSYNC-PUNCH-v1\n"

#: Default punch burst length (number of UDP packets per attempt).
DEFAULT_BURST_COUNT: Final[int] = 5

#: Default delay between consecutive burst packets.
DEFAULT_BURST_INTERVAL: Final[float] = 0.05

#: Default per-attempt handshake timeout.
DEFAULT_HANDSHAKE_TIMEOUT: Final[float] = 5.0

#: Default cool-off between the first and second attempt.
DEFAULT_RETRY_DELAY: Final[float] = 2.0


class HolePunchError(RelayError):
    """Raised when both hole-punch attempts fail to establish a QUIC connection."""


async def do_hole_punch(
    *,
    sock: _socket.socket,
    peer_addr: tuple[str, int],
    role: Literal["dialer", "listener"],
    configuration: QuicConfiguration,
    burst_count: int = DEFAULT_BURST_COUNT,
    burst_interval: float = DEFAULT_BURST_INTERVAL,
    handshake_timeout: float = DEFAULT_HANDSHAKE_TIMEOUT,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    max_attempts: int = 2,
) -> DialerEndpoint | ListenerEndpoint:
    """Execute the burst-then-QUIC hole-punch for one peer.

    Args:
        sock: A locally-bound UDP socket. Will be passed to
            ``loop.create_datagram_endpoint``; do not reuse it elsewhere
            for the duration of the call.
        peer_addr: Counterparty's observed UDP endpoint as reported by
            the relay.
        role: ``"dialer"`` or ``"listener"`` — assigned by the relay.
        configuration: A ``QuicConfiguration`` with ``is_client`` set to
            match ``role``. Caller owns the cert/key + ALPN setup.
        burst_count: Number of magic-byte UDP packets to send before
            the QUIC handshake on each attempt.
        burst_interval: Seconds between consecutive burst packets.
        handshake_timeout: Seconds to wait per attempt for the handshake.
        retry_delay: Seconds between the first and second attempt.
        max_attempts: Total attempts including the first one (default 2).

    Returns:
        The active endpoint once the handshake is complete.

    Raises:
        HolePunchError: If all attempts time out or fail.
        ValueError: If ``role`` is invalid or doesn't match ``configuration.is_client``.
    """
    if role == "dialer" and not configuration.is_client:
        raise ValueError("dialer role requires configuration.is_client=True")
    if role == "listener" and configuration.is_client:
        raise ValueError("listener role requires configuration.is_client=False")

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await _one_attempt(
                sock=sock,
                peer_addr=peer_addr,
                role=role,
                configuration=configuration,
                burst_count=burst_count,
                burst_interval=burst_interval,
                handshake_timeout=handshake_timeout,
            )
        except (TimeoutError, OSError) as exc:
            last_error = exc
            logger.warning(
                "hole-punch attempt %d/%d to %s failed: %s",
                attempt,
                max_attempts,
                peer_addr,
                exc,
            )
            if attempt < max_attempts:
                await asyncio.sleep(retry_delay)

    raise HolePunchError(
        f"hole-punch to {peer_addr} failed after {max_attempts} attempts: {last_error}"
    )


async def _one_attempt(
    *,
    sock: _socket.socket,
    peer_addr: tuple[str, int],
    role: Literal["dialer", "listener"],
    configuration: QuicConfiguration,
    burst_count: int,
    burst_interval: float,
    handshake_timeout: float,
) -> DialerEndpoint | ListenerEndpoint:
    """Run one burst + handshake pass and return the established endpoint.

    Closing an asyncio ``DatagramTransport`` closes the underlying socket,
    which would break retries on the same caller socket (and would drop the
    relay-observed NAT mapping in production). We duplicate the caller's
    socket per attempt: the dup shares the kernel socket (same local port,
    same NAT mapping) but has its own file descriptor we can close.
    """
    attempt_sock = _socket_mod.fromfd(
        sock.fileno(),
        _socket_mod.AF_INET,
        _socket_mod.SOCK_DGRAM,
    )
    attempt_sock.setblocking(False)

    if role == "dialer":
        try:
            # Burst FIRST so the listener's NAT is primed for the QUIC INITIAL.
            await _send_punch_burst(attempt_sock, peer_addr, burst_count, burst_interval)
            endpoint = await start_dialer(
                sock=attempt_sock,
                peer_addr=peer_addr,
                configuration=configuration,
            )
        except BaseException:
            attempt_sock.close()
            raise
        try:
            await asyncio.wait_for(
                endpoint.protocol.wait_connected(),
                timeout=handshake_timeout,
            )
        except TimeoutError:
            endpoint.transport.close()
            raise
        return endpoint

    # Listener path: bind the server first so it can answer the INITIAL
    # mid-burst, then send our own burst to open the local NAT mapping.
    try:
        endpoint = await start_listener(
            sock=attempt_sock,
            configuration=configuration,
        )
    except BaseException:
        attempt_sock.close()
        raise
    try:
        await _send_punch_burst(attempt_sock, peer_addr, burst_count, burst_interval)
        await endpoint.wait_accepted(timeout=handshake_timeout)
    except TimeoutError:
        endpoint.transport.close()
        raise
    try:
        await asyncio.wait_for(
            endpoint.protocol.wait_connected(),
            timeout=handshake_timeout,
        )
    except TimeoutError:
        endpoint.transport.close()
        raise
    return endpoint


async def _send_punch_burst(
    sock: _socket.socket,
    peer_addr: tuple[str, int],
    count: int,
    interval: float,
) -> None:
    """Fire ``count`` magic UDP packets toward ``peer_addr`` spaced by ``interval``."""
    for _ in range(count):
        sock.sendto(PUNCH_MAGIC, peer_addr)
        await asyncio.sleep(interval)


async def punch_via_endpoint(
    endpoint: MultiQuicEndpoint,
    peer_addr: tuple[str, int],
    *,
    count: int = DEFAULT_BURST_COUNT,
    interval: float = DEFAULT_BURST_INTERVAL,
) -> None:
    """Send a hole-punch burst over an already-bound :class:`MultiQuicEndpoint`.

    Sends ``count`` magic UDP datagrams toward ``peer_addr`` spaced by
    ``interval`` seconds. Because the datagrams leave from the multiplexed
    transport — the same one carrying the daemon's relay control channel
    — they share the relay-observed external NAT mapping, which is what
    makes hole punching actually open the pinhole on cone NATs.

    Cheap to call: ~``count * interval`` seconds, no allocations beyond
    the magic byte string.
    """
    for _ in range(count):
        endpoint.send_datagram(PUNCH_MAGIC, peer_addr)
        await asyncio.sleep(interval)
