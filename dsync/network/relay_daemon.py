"""Long-running daemon that fronts a peer's relay connection.

A ``RelayDaemon`` is what ``dsync relay connect`` runs in the foreground.
Responsibilities:

* Bind a UDP socket once and host **all** QUIC connections for this peer on
  it via :class:`MultiQuicEndpoint` — the relay control channel and any
  peer-to-peer data channels share the same socket so the relay-observed
  NAT mapping is preserved.
* Open the relay control channel, authenticate, and **stay** registered:
  exponential-backoff reconnect on drops, app-layer ``CONTROL_PING`` every
  ``KEEPALIVE_INTERVAL`` seconds so the NAT mapping doesn't expire.
* Accept incoming peer-to-peer dials when the relay pushes ``PUNCH_INFO``
  saying we're the listener for a sync. Run :class:`PeerSession` ``as_peer``
  to receive files.
* Expose a local IPC endpoint so ``dsync sync run_backup`` can ask us to
  outbound-sync a folder to a peer. We send ``CONNECT_REQUEST``, await the
  relay's ``PUNCH_INFO`` reply, open the peer-to-peer QUIC connection, and
  run :class:`PeerSession` ``as_source``.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import logging
import os
from typing import TYPE_CHECKING, Final

from dsync.network.errors import RelayAuthError, RelayError, RelayProtocolError
from dsync.network.hole_punch import HolePunchError, punch_via_endpoint
from dsync.network.local_ipc import (
    LocalControlServer,
    SyncFolderRequest,
    default_ipc_dir,
)
from dsync.network.multi_quic import MultiQuicEndpoint
from dsync.network.peer_auth import (
    extract_spki,
    fingerprint_from_spki,
    load_rsa_private_key,
    pack_auth_payload,
    sign_channel_binding,
)
from dsync.network.peer_session import PeerSession
from dsync.network.quic_core import (
    MsgType,
    build_quic_configuration,
    get_quic_channel_binding,
)
from dsync.network.relay_protocol import (
    ConnectRequest,
    PunchInfo,
    parse_error,
    parse_punch_info,
    parse_register_ack,
    recv_json,
    send_auth,
    send_json,
)

if TYPE_CHECKING:
    from pathlib import Path

    from aioquic.asyncio.protocol import QuicConnectionProtocol

    from dsync.config import RelayServer
    from dsync.state import AppState

logger = logging.getLogger(__name__)

#: Seconds between app-layer ``CONTROL_PING`` exchanges with the relay.
#: Consumer NATs commonly drop idle UDP mappings after 30-60 s; this stays
#: well below that. QUIC's own PING fires only on transport need so we
#: cannot rely on it alone.
KEEPALIVE_INTERVAL: Final[float] = 15.0

#: Seconds to wait for the ``CONTROL_PING`` reply before declaring the
#: relay channel dead and triggering a reconnect.
KEEPALIVE_REPLY_TIMEOUT: Final[float] = 5.0

#: Initial reconnect delay; doubled on each failure up to ``RECONNECT_BACKOFF_MAX``.
RECONNECT_BACKOFF_INITIAL: Final[float] = 1.0
RECONNECT_BACKOFF_MAX: Final[float] = 60.0

#: Outbound peer dial: max attempts including the first. The second attempt
#: re-bursts before retrying the QUIC handshake — gives the listener's NAT
#: a second chance to open the pinhole if the first burst raced badly.
PEER_DIAL_MAX_ATTEMPTS: Final[int] = 2

#: Per-attempt timeout for the peer-to-peer QUIC handshake.
PEER_DIAL_HANDSHAKE_TIMEOUT: Final[float] = 5.0

#: Backoff between the two peer-dial attempts.
PEER_DIAL_RETRY_DELAY: Final[float] = 1.5


@dataclass
class _PendingDial:
    """A peer is expected to dial us; we know its identity from PUNCH_INFO."""

    peer_fingerprint: str
    peer_id: str


class RelayDaemon:
    """Long-running daemon hosted by ``dsync relay connect``."""

    def __init__(
        self,
        *,
        relay: RelayServer,
        cert_path: Path | str,
        key_path: Path | str,
        state: AppState,
        recv_dir: Path,
        ipc_socket_path: Path | None = None,
    ) -> None:
        """Configure the daemon.

        Args:
            relay: The pinned relay entry (from ``relays.yaml``).
            cert_path: Local TLS certificate (PEM).
            key_path: Local RSA-2048 private key (PEM).
            state: Loaded ``AppState`` — used for ``devices.yaml`` lookups.
            recv_dir: Destination root for incoming files. Files land in
                ``recv_dir/<peer_id>/<basename>``.
            ipc_socket_path: Override for the IPC socket location; defaults
                to ``<XDG_RUNTIME_DIR>/dsync/relay-<pid>.sock``.
        """
        self._relay = relay
        self._cert_path = cert_path
        self._key_path = key_path
        self._state = state
        self._recv_dir = recv_dir
        self._ipc_socket_path = ipc_socket_path

        self._private_key = load_rsa_private_key(key_path)
        self._own_spki = extract_spki(self._private_key)
        self._own_fingerprint = fingerprint_from_spki(self._own_spki)

        self._endpoint: MultiQuicEndpoint | None = None
        self._relay_protocol: QuicConnectionProtocol | None = None
        self._relay_connected = asyncio.Event()
        self._ipc_server: LocalControlServer | None = None
        # Per-peer-addr expected-dial info populated by relay PUNCH_INFO pushes.
        self._pending_dials: dict[tuple[str, int], _PendingDial] = {}
        # Active background tasks; kept alive so they aren't gc'd.
        self._tasks: set[asyncio.Task[object]] = set()
        self._shutdown = asyncio.Event()

    @property
    def fingerprint(self) -> str:
        """SHA-256 hex fingerprint of this daemon's own public key."""
        return self._own_fingerprint

    @property
    def local_addr(self) -> tuple[str, int]:
        """The (host, port) the daemon is listening on."""
        if self._endpoint is None:
            raise RelayError("daemon not running")
        return self._endpoint.local_addr

    @property
    def is_relay_connected(self) -> bool:
        """True when the relay control channel is currently authenticated."""
        return self._relay_connected.is_set()

    async def start(self) -> None:
        """Bind the socket, run the first relay handshake, start background loops.

        Startup is **synchronous**: if the first connection or AUTH fails, the
        exception propagates so the CLI can surface a clear error. Once the
        first attempt succeeds, the maintenance and keepalive loops take over.
        """
        self._endpoint = await MultiQuicEndpoint.bind(host="0.0.0.0", port=0)  # nosec B104
        self._endpoint.enable_server(
            build_quic_configuration(
                is_client=False,
                cert_path=self._cert_path,
                key_path=self._key_path,
            ),
        )

        # First connection synchronously — fail fast on bad config.
        await self._connect_relay_once()

        # Long-running supervisors.
        self._spawn(self._incoming_dial_loop())
        self._spawn(self._maintain_relay())
        self._spawn(self._keepalive_loop())

        # IPC server.
        socket_path = self._ipc_socket_path or (default_ipc_dir() / f"relay-{os.getpid()}.sock")
        self._ipc_server = LocalControlServer(
            socket_path=socket_path,
            handler=self._handle_ipc_request,
        )
        await self._ipc_server.start()

        logger.info(
            "RelayDaemon up: own fp=%s, relay=%s:%d, local=%s, ipc=%s",
            self._own_fingerprint,
            self._relay.host,
            self._relay.port,
            self.local_addr,
            socket_path,
        )

    async def close(self) -> None:
        """Stop the IPC server, tear down QUIC connections, release the socket."""
        self._shutdown.set()
        self._relay_connected.clear()
        if self._ipc_server is not None:
            await self._ipc_server.close()
            self._ipc_server = None
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(BaseException):
                await task
        if self._endpoint is not None:
            await self._endpoint.close()
            self._endpoint = None

    async def wait_until_shutdown(self) -> None:
        """Block until ``close()`` is called (or ``shutdown()`` from elsewhere)."""
        await self._shutdown.wait()

    # ---- relay control channel: connect / authenticate ----------------------

    async def _connect_relay_once(self) -> None:
        """Open + authenticate the relay control channel.

        Idempotent: any stale routing entry for the relay's addr is dropped
        first, so this method can be called both at startup and during
        reconnect without leaking state.
        """
        assert self._endpoint is not None
        relay_addr = (self._relay.host, self._relay.port)
        # Drop any stale entry from a previous (failed) attempt.
        self._endpoint.remove_connection(relay_addr)

        self._relay_protocol = await self._endpoint.add_outgoing(
            relay_addr,
            build_quic_configuration(
                is_client=True,
                cert_path=self._cert_path,
                key_path=self._key_path,
            ),
            stream_handler=self._on_relay_initiated_stream,
        )
        await asyncio.wait_for(self._relay_protocol.wait_connected(), timeout=10.0)
        self._verify_relay_cert()
        await self._authenticate_to_relay()
        self._relay_connected.set()

    def _verify_relay_cert(self) -> None:
        """Compare relay's TLS-presented SPKI fingerprint to ``relays.yaml``."""
        assert self._relay_protocol is not None
        cert = getattr(self._relay_protocol._quic.tls, "_peer_certificate", None)
        if cert is None:
            raise RelayAuthError("relay did not present a TLS certificate")
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        spki = cert.public_key().public_bytes(
            Encoding.DER,
            PublicFormat.SubjectPublicKeyInfo,
        )
        actual = fingerprint_from_spki(spki)
        if actual != self._relay.fingerprint:
            raise RelayAuthError(
                f"relay fingerprint mismatch: expected {self._relay.fingerprint}, got {actual}"
            )

    async def _authenticate_to_relay(self) -> None:
        """Open an AUTH stream, sign the channel binding, read REGISTER_ACK."""
        assert self._relay_protocol is not None
        reader, writer = await self._relay_protocol.create_stream()
        binding = get_quic_channel_binding(self._relay_protocol._quic)
        signature = sign_channel_binding(self._private_key, binding)
        await send_auth(writer, pack_auth_payload(self._own_spki, signature))

        msg_type, body = await recv_json(reader)
        if msg_type == MsgType.ERROR:
            raise RelayAuthError(f"relay rejected AUTH: {parse_error(body).reason}")
        if msg_type != MsgType.REGISTER_ACK:
            raise RelayProtocolError(f"expected REGISTER_ACK, got msg type {msg_type}")
        ack = parse_register_ack(body)
        logger.info(
            "registered to relay; observed endpoint %s:%d",
            ack.observed_host,
            ack.observed_port,
        )

    # ---- supervisors ---------------------------------------------------------

    async def _maintain_relay(self) -> None:
        """Block on the live relay protocol; on drop, exponential-backoff reconnect.

        First connection is owned by :meth:`start`, not this loop. The loop
        starts in the "connected" state, watches for the protocol to die,
        clears the connected flag, then attempts to reconnect.
        """
        backoff = RECONNECT_BACKOFF_INITIAL
        while not self._shutdown.is_set():
            # Wait for the current relay protocol to die.
            if self._relay_protocol is not None:
                try:
                    await self._relay_protocol.wait_closed()
                except asyncio.CancelledError:
                    return
            self._relay_connected.clear()
            self._relay_protocol = None
            if self._shutdown.is_set():
                return
            logger.warning("relay control channel dropped; reconnecting in %.1fs", backoff)
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                return
            try:
                await self._connect_relay_once()
                logger.info("relay control channel re-established")
                backoff = RECONNECT_BACKOFF_INITIAL
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("reconnect attempt failed: %s", exc)
                backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)

    async def _keepalive_loop(self) -> None:
        """Send a ``CONTROL_PING`` every :data:`KEEPALIVE_INTERVAL` seconds.

        On reply timeout, close the relay protocol so the maintenance loop
        notices and reconnects.
        """
        while not self._shutdown.is_set():
            try:
                await asyncio.sleep(KEEPALIVE_INTERVAL)
            except asyncio.CancelledError:
                return
            if self._shutdown.is_set():
                return
            if not self._relay_connected.is_set() or self._relay_protocol is None:
                continue
            protocol = self._relay_protocol
            try:
                reader, writer = await protocol.create_stream()
                await send_json(writer, MsgType.CONTROL_PING, None)
                await asyncio.wait_for(recv_json(reader), timeout=KEEPALIVE_REPLY_TIMEOUT)
                logger.debug("keepalive ping ok")
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("keepalive ping failed (%s); forcing reconnect", exc)
                with contextlib.suppress(Exception):
                    protocol.close()

    # ---- relay-pushed streams ------------------------------------------------

    def _on_relay_initiated_stream(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Stream-handler for streams the relay pushes at us (PUNCH_INFO etc.)."""
        self._spawn(self._handle_relay_push(reader, writer))

    async def _handle_relay_push(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            try:
                msg_type, body = await recv_json(reader)
            except Exception as exc:
                logger.warning("relay-pushed stream malformed: %s", exc)
                return

            if msg_type == MsgType.PUNCH_INFO:
                punch = parse_punch_info(body)
                await self._handle_incoming_punch_info(punch)
            elif msg_type == MsgType.CONTROL_PING:
                with contextlib.suppress(Exception):
                    await send_json(writer, MsgType.CONTROL_PING, None)
            else:
                logger.warning("ignoring relay-pushed msg type %s", msg_type)
        finally:
            writer.close()

    async def _handle_incoming_punch_info(self, punch: PunchInfo) -> None:
        """We're the listener for an inbound sync from ``punch.peer_*``.

        We *also* hole-punch from this side: a burst of magic UDP datagrams
        toward the dialer's relay-observed addr primes **our** NAT so the
        dialer's QUIC INITIAL is allowed back in. The relay sends
        ``PUNCH_INFO`` to dialer and listener simultaneously, so both
        bursts overlap and both pinholes open in time for the handshake.
        """
        if punch.role != "listener":
            logger.warning("ignoring PUNCH_INFO with unexpected role %s", punch.role)
            return
        peer_id = self._device_id_for_fingerprint(punch.peer_fingerprint)
        if peer_id is None:
            logger.warning(
                "relay pushed PUNCH_INFO for untrusted fingerprint %s; ignoring",
                punch.peer_fingerprint,
            )
            return
        peer_addr = (punch.peer_host, punch.peer_port)
        self._pending_dials[peer_addr] = _PendingDial(
            peer_fingerprint=punch.peer_fingerprint,
            peer_id=peer_id,
        )
        logger.info(
            "expecting inbound dial from %s (%s) at %s:%d",
            peer_id,
            punch.peer_fingerprint,
            punch.peer_host,
            punch.peer_port,
        )
        # Fire the listener-side burst toward the dialer. Best-effort; if it
        # fails (e.g. transient socket error) we still keep the pending dial
        # registered — the dialer may still get through if their burst
        # happened to win the race.
        if self._endpoint is not None:
            try:
                await punch_via_endpoint(self._endpoint, peer_addr)
            except Exception as exc:
                logger.warning("listener-side punch burst failed: %s", exc)

    # ---- inbound peer-to-peer dials -----------------------------------------

    async def _incoming_dial_loop(self) -> None:
        """Accept new peer dials and drive PeerSession.as_peer for each."""
        assert self._endpoint is not None
        while not self._shutdown.is_set():
            try:
                accepted = await self._endpoint.accept_next(timeout=None)
            except asyncio.CancelledError:
                return
            self._spawn(self._handle_inbound_peer(accepted))

    async def _handle_inbound_peer(
        self,
        accepted: QuicConnectionProtocol,
    ) -> None:
        assert self._endpoint is not None
        try:
            try:
                await asyncio.wait_for(accepted.wait_connected(), timeout=15.0)
            except TimeoutError:
                logger.warning("inbound peer never completed handshake; dropping")
                return

            # Identify the peer via _pending_dials populated by PUNCH_INFO.
            peer_addr = accepted._quic._network_paths[0].addr
            pending = self._pending_dials.pop(peer_addr, None)
            if pending is None:
                logger.warning(
                    "inbound peer at %s without matching PUNCH_INFO; dropping",
                    peer_addr,
                )
                return

            # Wait for the peer's first stream (their AUTH frame).
            stream_pair = await _wait_for_first_stream(accepted, timeout=15.0)
            if stream_pair is None:
                logger.warning("inbound peer opened no stream; dropping")
                return
            reader, writer = stream_pair

            session = PeerSession.as_peer(
                cert_path=self._cert_path,
                key_path=self._key_path,
                state=self._state,
                recv_dir=self._recv_dir,
            )
            try:
                await session.run(
                    reader,
                    writer,
                    accepted._quic,
                    expected_peer_fingerprint=pending.peer_fingerprint,
                )
                logger.info("inbound sync from %s complete", pending.peer_id)
            except Exception:
                logger.exception("inbound sync from %s failed", pending.peer_id)
        finally:
            self._endpoint.remove_connection_by_protocol(accepted)
            accepted.close()

    # ---- IPC ----------------------------------------------------------------

    async def _handle_ipc_request(
        self,
        payload: dict[str, object],
    ) -> dict[str, object]:
        op = payload.get("op")
        if op == "sync_folder":
            try:
                request = SyncFolderRequest.model_validate(payload)
            except Exception as exc:
                return {"status": "error", "reason": f"bad request: {exc}"}
            return await self._do_sync_folder(request)
        if op == "status":
            return {
                "status": "ok",
                "reason": "connected" if self._relay_connected.is_set() else "reconnecting",
            }
        return {"status": "error", "reason": f"unknown op: {op!r}"}

    async def _do_sync_folder(self, request: SyncFolderRequest) -> dict[str, object]:
        if not self._relay_connected.is_set():
            return {
                "status": "error",
                "reason": "relay control channel is not currently connected; try again shortly",
            }
        device = next(
            (d for d in self._state.devices.trusted_devices if d.id == request.peer_id),
            None,
        )
        if device is None:
            return {"status": "error", "reason": f"unknown peer id {request.peer_id!r}"}
        if device.relay_id is not None and device.relay_id != self._relay.id:
            return {
                "status": "error",
                "reason": (
                    f"peer {device.id} is configured for relay {device.relay_id!r}, "
                    f"daemon is connected to {self._relay.id!r}"
                ),
            }
        folder = next(
            (f for f in self._state.folders.entries if f.id == request.folder_id),
            None,
        )
        if folder is None:
            return {
                "status": "error",
                "reason": f"unknown folder id {request.folder_id!r}",
            }
        if folder.devices is not None and device.id not in folder.devices:
            return {
                "status": "error",
                "reason": f"folder {folder.id!r} is not configured for peer {device.id!r}",
            }

        try:
            await self._run_outbound_sync(
                folder=folder,
                peer_device=device,
            )
        except Exception as exc:
            logger.exception("outbound sync failed")
            return {"status": "error", "reason": str(exc)}
        return {"status": "ok", "reason": None}

    async def _run_outbound_sync(self, *, folder, peer_device) -> None:  # type: ignore[no-untyped-def]
        """CONNECT_REQUEST → PUNCH_INFO → open peer QUIC → PeerSession.as_source."""
        if self._relay_protocol is None or not self._relay_connected.is_set():
            raise RelayError("relay control channel is not connected")
        assert self._endpoint is not None
        relay_protocol = self._relay_protocol

        # 1. CONNECT_REQUEST on the relay control channel.
        req_reader, req_writer = await relay_protocol.create_stream()
        await send_json(
            req_writer,
            MsgType.CONNECT_REQUEST,
            ConnectRequest(target_fingerprint=peer_device.fingerprint),
        )
        msg_type, body = await recv_json(req_reader)
        if msg_type == MsgType.ERROR:
            raise RelayError(f"relay error: {parse_error(body).reason}")
        if msg_type != MsgType.PUNCH_INFO:
            raise RelayProtocolError(f"unexpected response type {msg_type}")
        punch = parse_punch_info(body)
        if punch.role != "dialer":
            raise RelayProtocolError(f"relay assigned us role={punch.role}, expected dialer")
        if punch.peer_fingerprint != peer_device.fingerprint:
            raise RelayProtocolError(
                f"PUNCH_INFO fingerprint mismatch: expected {peer_device.fingerprint}, "
                f"got {punch.peer_fingerprint}"
            )

        # 2. Hole-punch + open a peer-to-peer QUIC connection over the SAME
        # socket (preserves the relay-observed NAT mapping). The burst opens
        # the pinhole on both peers' NATs; the listener side burstings in
        # parallel via _handle_incoming_punch_info.
        peer_addr = (punch.peer_host, punch.peer_port)
        peer_protocol = await self._dial_peer_with_punch(peer_addr)

        # 3. Run source-side session on a fresh stream.
        sess_reader, sess_writer = await peer_protocol.create_stream()
        session = PeerSession.as_source(
            cert_path=self._cert_path,
            key_path=self._key_path,
            state=self._state,
            folder=folder,
        )
        try:
            await session.run(
                sess_reader,
                sess_writer,
                peer_protocol._quic,
                expected_peer_fingerprint=peer_device.fingerprint,
            )
        finally:
            # Tear down the peer connection — leave the socket alive (relay uses it).
            self._endpoint.remove_connection(peer_addr)
            peer_protocol.close()

    async def _dial_peer_with_punch(
        self,
        peer_addr: tuple[str, int],
    ) -> QuicConnectionProtocol:
        """Burst-then-QUIC the peer-to-peer connection, retrying once on failure.

        On real cone NATs neither side accepts inbound from the other until
        each has sent at least one outbound datagram toward the other — so
        we burst before the QUIC INITIAL leaves. A second attempt gives the
        listener's NAT a second chance if the first burst arrived after the
        listener's pinhole had closed (rare on healthy networks but cheap
        insurance).
        """
        assert self._endpoint is not None
        config = build_quic_configuration(
            is_client=True,
            cert_path=self._cert_path,
            key_path=self._key_path,
        )

        last_error: BaseException | None = None
        for attempt in range(1, PEER_DIAL_MAX_ATTEMPTS + 1):
            # Always start each attempt with a clean routing entry.
            self._endpoint.remove_connection(peer_addr)
            try:
                await punch_via_endpoint(self._endpoint, peer_addr)
                peer_protocol = await self._endpoint.add_outgoing(peer_addr, config)
                await asyncio.wait_for(
                    peer_protocol.wait_connected(),
                    timeout=PEER_DIAL_HANDSHAKE_TIMEOUT,
                )
            except (TimeoutError, ConnectionError, OSError) as exc:
                last_error = exc
                logger.warning(
                    "peer dial attempt %d/%d to %s failed: %s",
                    attempt,
                    PEER_DIAL_MAX_ATTEMPTS,
                    peer_addr,
                    exc,
                )
                # Tear down the partially-attached protocol before retrying.
                self._endpoint.remove_connection(peer_addr)
                if attempt < PEER_DIAL_MAX_ATTEMPTS:
                    await asyncio.sleep(PEER_DIAL_RETRY_DELAY)
            else:
                return peer_protocol

        raise HolePunchError(
            f"peer dial to {peer_addr} failed after {PEER_DIAL_MAX_ATTEMPTS} attempts: {last_error}"
        )

    # ---- helpers ------------------------------------------------------------

    def _device_id_for_fingerprint(self, fingerprint: str) -> str | None:
        for device in self._state.devices.trusted_devices:
            if device.fingerprint == fingerprint:
                return device.id
        return None

    def _spawn(self, coro: object) -> None:
        task: asyncio.Task[object] = asyncio.create_task(coro)  # type: ignore[arg-type]
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


async def _wait_for_first_stream(
    protocol: QuicConnectionProtocol,
    timeout: float,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
    """Block until the peer opens its first stream on ``protocol``.

    The original QuicConnectionProtocol's stream_handler is replaced with
    a one-shot capture; if a handler was already in place this won't
    interfere with subsequent streams (none are expected for a one-shot
    sync session).
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = loop.create_future()

    def capture(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        if not future.done():
            future.set_result((reader, writer))

    protocol._stream_handler = capture
    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except TimeoutError:
        return None
