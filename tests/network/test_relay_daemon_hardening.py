"""Tests for ``dsync.network.relay_daemon`` resilience features.

Covers:
* auto-reconnect after the server-side closes the relay control channel,
* ``sync_folder`` IPC returning a clean error while the daemon is
  between connections (rather than crashing or hanging).

The keepalive loop's positive path is not exercised here — its happy case
is observed in the smoke logs at ``DEBUG`` level; its negative path
(forcing a reconnect on missed ping) requires simulating a dead-but-not-
gracefully-closed relay, which is awkward without a dedicated harness.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import os
from typing import TYPE_CHECKING

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from dsync.config import (
    DevicesConfig,
    FoldersConfig,
    RelaysConfig,
    RelayServer,
)
from dsync.network.local_ipc import LocalControlClient, SyncFolderRequest
from dsync.network.relay_daemon import RelayDaemon
from dsync.network.relay_server import RelayServer as RelayQuicServer
from dsync.state import AppState

if TYPE_CHECKING:
    from pathlib import Path


def _write_self_signed(cert_path: Path, key_path: Path) -> str:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dsync-test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    spki = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(spki).hexdigest()


def _build_state(
    *,
    config_dir: Path,
    relay: RelayServer,
) -> AppState:
    return AppState(
        config_dir=config_dir,
        folders=FoldersConfig(entries=[]),
        devices=DevicesConfig(trusted_devices=[]),
        relays=RelaysConfig(relays=[relay]),
    )


async def _wait_until(
    condition: object,
    *,
    timeout: float = 10.0,
    interval: float = 0.05,
) -> None:
    """Poll ``condition`` until it becomes truthy or ``timeout`` elapses."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        if condition():  # type: ignore[operator]
            return
        if asyncio.get_event_loop().time() >= deadline:
            raise TimeoutError("wait_until timed out")
        await asyncio.sleep(interval)


async def test_daemon_reconnects_after_server_side_close(tmp_path: Path) -> None:
    """Closing the server-side protocol forces the daemon's _maintain_relay path."""
    relay_cert = tmp_path / "relay-cert.pem"
    relay_key = tmp_path / "relay-key.pem"
    relay_fp = _write_self_signed(relay_cert, relay_key)
    relay_quic = RelayQuicServer(
        host="127.0.0.1", port=0, cert_path=relay_cert, key_path=relay_key,
    )
    await relay_quic.start()
    relay_entry = RelayServer(
        id="relay-test",
        host="127.0.0.1",
        port=relay_quic.bound_port,
        fingerprint=relay_fp,
    )

    peer_cert = tmp_path / "peer-cert.pem"
    peer_key = tmp_path / "peer-key.pem"
    _write_self_signed(peer_cert, peer_key)
    state = _build_state(config_dir=tmp_path, relay=relay_entry)

    daemon = RelayDaemon(
        relay=relay_entry,
        cert_path=peer_cert,
        key_path=peer_key,
        state=state,
        recv_dir=tmp_path / "recv",
        ipc_socket_path=tmp_path / f"daemon-{os.getpid()}.sock",
    )
    (tmp_path / "recv").mkdir(exist_ok=True)

    try:
        await daemon.start()
        assert daemon.is_relay_connected

        # Reach into the relay's registry and forcibly close the server-side
        # QuicConnectionProtocol for this peer. The daemon's wait_closed()
        # will fire and the maintenance loop should kick off a reconnect.
        registered = await relay_quic.registered_fingerprints()
        assert len(registered) == 1
        async with relay_quic._registry_lock:
            server_side_protocol = next(iter(relay_quic._registry.values())).protocol
        server_side_protocol.close()

        # The connected flag should clear quickly...
        await _wait_until(lambda: not daemon.is_relay_connected, timeout=5.0)
        # ...and then come back as the maintenance loop reconnects.
        await _wait_until(lambda: daemon.is_relay_connected, timeout=10.0)
    finally:
        await daemon.close()
        await relay_quic.close()


async def test_sync_folder_rejected_while_disconnected(tmp_path: Path) -> None:
    """IPC requests get a clean error when the relay channel is between connections."""
    relay_cert = tmp_path / "relay-cert.pem"
    relay_key = tmp_path / "relay-key.pem"
    relay_fp = _write_self_signed(relay_cert, relay_key)
    relay_quic = RelayQuicServer(
        host="127.0.0.1", port=0, cert_path=relay_cert, key_path=relay_key,
    )
    await relay_quic.start()
    relay_entry = RelayServer(
        id="relay-test",
        host="127.0.0.1",
        port=relay_quic.bound_port,
        fingerprint=relay_fp,
    )

    peer_cert = tmp_path / "peer-cert.pem"
    peer_key = tmp_path / "peer-key.pem"
    _write_self_signed(peer_cert, peer_key)
    state = _build_state(config_dir=tmp_path, relay=relay_entry)

    daemon = RelayDaemon(
        relay=relay_entry,
        cert_path=peer_cert,
        key_path=peer_key,
        state=state,
        recv_dir=tmp_path / "recv",
        ipc_socket_path=tmp_path / f"daemon-{os.getpid()}.sock",
    )
    (tmp_path / "recv").mkdir(exist_ok=True)

    try:
        await daemon.start()
        assert daemon.is_relay_connected

        # Take the relay down entirely. The daemon's wait_closed() should fire
        # via QUIC close; the maintenance loop will then loop on reconnect
        # attempts that all fail.
        await relay_quic.close()
        await _wait_until(lambda: not daemon.is_relay_connected, timeout=10.0)

        client = LocalControlClient(socket_path=daemon._ipc_server.socket_path)
        response = await client.request(
            SyncFolderRequest(folder_id="anything", peer_id="anything"),
        )
        assert response.status == "error"
        assert response.reason is not None
        assert "not currently connected" in response.reason
    finally:
        await daemon.close()


async def test_keepalive_fires_periodically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a small KEEPALIVE_INTERVAL, multiple CONTROL_PING streams arrive at the relay."""
    from dsync.network import relay_daemon as rd
    from dsync.network import relay_server as rs
    from dsync.network.quic_core import MsgType

    # Run the keepalive loop on a tight cadence so the test stays snappy.
    monkeypatch.setattr(rd, "KEEPALIVE_INTERVAL", 0.2)
    monkeypatch.setattr(rd, "KEEPALIVE_REPLY_TIMEOUT", 1.0)

    # Count CONTROL_PING streams reaching the relay by wrapping its handler.
    ping_count = 0
    original_run_stream = rs.RelayServer._run_stream  # noqa: SLF001

    async def counting_run_stream(self, reader, writer):
        nonlocal ping_count
        # Peek the first byte; we have to put it back-ish — replicate the
        # legacy dispatch but increment when we see a PING.
        data_so_far = b""
        try:
            type_byte = await reader.readexactly(1)
        except Exception:
            return
        data_so_far += type_byte
        if type_byte[0] == MsgType.CONTROL_PING.value:
            ping_count += 1
        # Re-inject the byte and delegate to the real handler.
        new_reader = asyncio.StreamReader()
        new_reader.feed_data(data_so_far)

        # Drain remaining from original reader and feed forward.
        async def pump() -> None:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    new_reader.feed_eof()
                    return
                new_reader.feed_data(chunk)

        pump_task = asyncio.create_task(pump())
        try:
            await original_run_stream(self, new_reader, writer)
        finally:
            pump_task.cancel()

    monkeypatch.setattr(rs.RelayServer, "_run_stream", counting_run_stream)

    relay_cert = tmp_path / "relay-cert.pem"
    relay_key = tmp_path / "relay-key.pem"
    relay_fp = _write_self_signed(relay_cert, relay_key)
    relay_quic = rs.RelayServer(
        host="127.0.0.1", port=0, cert_path=relay_cert, key_path=relay_key,
    )
    await relay_quic.start()
    relay_entry = RelayServer(
        id="relay-test",
        host="127.0.0.1",
        port=relay_quic.bound_port,
        fingerprint=relay_fp,
    )

    peer_cert = tmp_path / "peer-cert.pem"
    peer_key = tmp_path / "peer-key.pem"
    _write_self_signed(peer_cert, peer_key)
    state = _build_state(config_dir=tmp_path, relay=relay_entry)
    daemon = RelayDaemon(
        relay=relay_entry,
        cert_path=peer_cert,
        key_path=peer_key,
        state=state,
        recv_dir=tmp_path / "recv",
        ipc_socket_path=tmp_path / f"daemon-{os.getpid()}.sock",
    )
    (tmp_path / "recv").mkdir(exist_ok=True)

    try:
        await daemon.start()
        # Sleep long enough for ~5 keepalive intervals.
        await asyncio.sleep(1.2)
        # At minimum 3 pings should have landed (5 intervals × 0.2 s = 1.0 s);
        # be generous to absorb event-loop scheduling jitter.
        assert ping_count >= 3, f"expected ≥3 keepalive pings, got {ping_count}"
    finally:
        await daemon.close()
        await relay_quic.close()


async def test_keepalive_constants_are_sensible() -> None:
    """Catch accidental edits that would defeat the keepalive's purpose."""
    from dsync.network import relay_daemon

    # Consumer NAT mappings die in 30–60 s; we must ping well under that.
    assert 0 < relay_daemon.KEEPALIVE_INTERVAL <= 25.0
    assert 0 < relay_daemon.KEEPALIVE_REPLY_TIMEOUT < relay_daemon.KEEPALIVE_INTERVAL
    # Backoff must grow but stay bounded.
    assert relay_daemon.RECONNECT_BACKOFF_INITIAL >= 0.5
    assert relay_daemon.RECONNECT_BACKOFF_MAX <= 120.0
