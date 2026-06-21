"""Direct-QUIC integration tests for ``dsync.network.peer_session``.

These exercise the full source ↔ peer flow over a loopback QUIC connection
without involving the relay or the hole-punch coordinator:

* dialer opens a session stream, runs ``PeerSession.as_source`` and sends a
  file;
* listener accepts that stream via its stream handler, runs
  ``PeerSession.as_peer`` and writes the file under ``recv_dir/<peer_id>/``.

We also verify the legacy direction-violation policy still fires when the
caller tries to run the wrong side of a backup session.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import hashlib
import socket
from typing import TYPE_CHECKING

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from dsync.config import (
    DevicesConfig,
    FolderEntry,
    FoldersConfig,
    RelaysConfig,
    RelayServer,
    SyncMode,
    TrustedDevice,
)
from dsync.network.backup_direction import (
    BackupSession,
    DirectionViolationError,
)
from dsync.network.errors import PeerAuthError
from dsync.network.peer_session import PeerSession
from dsync.network.quic_core import build_quic_configuration
from dsync.network.quic_transport import start_dialer, start_listener
from dsync.state import AppState

if TYPE_CHECKING:
    from pathlib import Path


def _write_self_signed(cert_path: Path, key_path: Path) -> str:
    """Write a fresh RSA-2048 self-signed cert + key. Returns SPKI fingerprint."""
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


def _bound_udp_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    return sock


def _make_app_state(
    *,
    config_dir: Path,
    trusted: list[tuple[str, str]],
) -> AppState:
    """Build an AppState whose devices.yaml lists ``trusted`` (id, fingerprint) pairs."""
    relays = RelaysConfig(
        relays=[
            RelayServer(
                id="relay-test",
                host="127.0.0.1",
                port=1,
                fingerprint="hex-" + "0" * 64,
            )
        ]
    )
    devices = DevicesConfig(
        trusted_devices=[
            TrustedDevice(id=pid, fingerprint=fp, relay_id="relay-test") for pid, fp in trusted
        ]
    )
    folders = FoldersConfig(entries=[])
    return AppState(
        config_dir=config_dir,
        folders=folders,
        devices=devices,
        relays=relays,
    )


async def test_source_to_peer_file_transfer(tmp_path: Path) -> None:
    """End-to-end: source dials, peer listens, one file lands intact."""
    # Identities
    src_cert = tmp_path / "src-cert.pem"
    src_key = tmp_path / "src-key.pem"
    src_fp = _write_self_signed(src_cert, src_key)

    peer_cert = tmp_path / "peer-cert.pem"
    peer_key = tmp_path / "peer-key.pem"
    peer_fp = _write_self_signed(peer_cert, peer_key)

    # Data to send
    src_folder = tmp_path / "src-folder"
    src_folder.mkdir()
    payload = b"hello relay-and-quic world\n" * 4096  # ~104 KB → spans many chunks
    (src_folder / "hello.bin").write_bytes(payload)
    expected_digest = hashlib.sha256(payload).hexdigest()

    recv_dir = tmp_path / "recv"
    recv_dir.mkdir()

    # AppStates: each side trusts the other
    src_state = _make_app_state(
        config_dir=tmp_path,
        trusted=[("peer-device", peer_fp)],
    )
    peer_state = _make_app_state(
        config_dir=tmp_path,
        trusted=[("source-device", src_fp)],
    )

    folder_entry = FolderEntry(
        id="hello-folder",
        path=src_folder,
        mode=SyncMode.BACKUP_TO_PEER,
        devices=["peer-device"],
        recursive=False,
    )

    # QUIC sockets
    dialer_sock = _bound_udp_socket()
    listener_sock = _bound_udp_socket()
    listener_addr = listener_sock.getsockname()

    dialer_cfg = build_quic_configuration(is_client=True, cert_path=src_cert, key_path=src_key)
    listener_cfg = build_quic_configuration(is_client=False, cert_path=peer_cert, key_path=peer_key)

    # The listener-side session stream future is filled by the stream handler
    # the very first time the dialer opens a stream.
    loop = asyncio.get_running_loop()
    listener_stream: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = (
        loop.create_future()
    )

    def on_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if not listener_stream.done():
            listener_stream.set_result((reader, writer))

    dialer_endpoint = None
    listener_endpoint = None
    try:
        listener_endpoint = await start_listener(
            sock=listener_sock,
            configuration=listener_cfg,
            stream_handler=on_stream,
        )
        dialer_endpoint = await start_dialer(
            sock=dialer_sock,
            peer_addr=listener_addr,
            configuration=dialer_cfg,
        )
        accepted_protocol = await listener_endpoint.wait_accepted(timeout=5.0)
        await asyncio.gather(
            dialer_endpoint.protocol.wait_connected(),
            accepted_protocol.wait_connected(),
        )

        # Open the session stream from the dialer side. The listener won't
        # see it until the source writes its first AUTH bytes, so we drive
        # source and peer sessions concurrently below.
        dialer_reader, dialer_writer = await dialer_endpoint.protocol.create_stream()

        source_session = PeerSession.as_source(
            cert_path=src_cert,
            key_path=src_key,
            state=src_state,
            folder=folder_entry,
        )
        peer_session = PeerSession.as_peer(
            cert_path=peer_cert,
            key_path=peer_key,
            state=peer_state,
            recv_dir=recv_dir,
        )

        async def run_source() -> str:
            return await source_session.run(
                dialer_reader,
                dialer_writer,
                dialer_endpoint.protocol._quic,
                expected_peer_fingerprint=peer_fp,
            )

        async def run_peer() -> str:
            peer_reader, peer_writer = await asyncio.wait_for(listener_stream, timeout=5.0)
            return await peer_session.run(
                peer_reader,
                peer_writer,
                accepted_protocol._quic,
                expected_peer_fingerprint=src_fp,
            )

        verified_on_source, verified_on_peer = await asyncio.gather(
            run_source(),
            run_peer(),
        )
        assert verified_on_source == "peer-device"
        assert verified_on_peer == "source-device"

        # File should now exist under recv_dir/<source-device>/hello.bin
        received_path = recv_dir / "source-device" / "hello.bin"
        assert received_path.is_file()
        assert hashlib.sha256(received_path.read_bytes()).hexdigest() == expected_digest
    finally:
        if dialer_endpoint is not None:
            with contextlib.suppress(Exception):
                dialer_endpoint.transport.close()
        if listener_endpoint is not None:
            with contextlib.suppress(Exception):
                listener_endpoint.transport.close()
        dialer_sock.close()
        listener_sock.close()


async def test_unknown_peer_fingerprint_is_rejected(tmp_path: Path) -> None:
    """A peer whose fingerprint isn't in devices.yaml fails AUTH cleanly."""
    src_cert = tmp_path / "src-cert.pem"
    src_key = tmp_path / "src-key.pem"
    _write_self_signed(src_cert, src_key)
    peer_cert = tmp_path / "peer-cert.pem"
    peer_key = tmp_path / "peer-key.pem"
    _write_self_signed(peer_cert, peer_key)

    src_folder = tmp_path / "src-folder"
    src_folder.mkdir()
    (src_folder / "tiny.bin").write_bytes(b"x")
    recv_dir = tmp_path / "recv"
    recv_dir.mkdir()

    # Neither side trusts the other (no devices in either AppState).
    empty_state = _make_app_state(config_dir=tmp_path, trusted=[])

    folder_entry = FolderEntry(
        id="hello",
        path=src_folder,
        mode=SyncMode.BACKUP_TO_PEER,
        devices=[],
        recursive=False,
    )

    dialer_sock = _bound_udp_socket()
    listener_sock = _bound_udp_socket()
    listener_addr = listener_sock.getsockname()

    dialer_cfg = build_quic_configuration(is_client=True, cert_path=src_cert, key_path=src_key)
    listener_cfg = build_quic_configuration(is_client=False, cert_path=peer_cert, key_path=peer_key)

    loop = asyncio.get_running_loop()
    listener_stream: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = (
        loop.create_future()
    )

    def on_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if not listener_stream.done():
            listener_stream.set_result((reader, writer))

    dialer_endpoint = None
    listener_endpoint = None
    try:
        listener_endpoint = await start_listener(
            sock=listener_sock,
            configuration=listener_cfg,
            stream_handler=on_stream,
        )
        dialer_endpoint = await start_dialer(
            sock=dialer_sock,
            peer_addr=listener_addr,
            configuration=dialer_cfg,
        )
        accepted_protocol = await listener_endpoint.wait_accepted(timeout=5.0)
        await asyncio.gather(
            dialer_endpoint.protocol.wait_connected(),
            accepted_protocol.wait_connected(),
        )

        dialer_reader, dialer_writer = await dialer_endpoint.protocol.create_stream()

        source_session = PeerSession.as_source(
            cert_path=src_cert,
            key_path=src_key,
            state=empty_state,
            folder=folder_entry,
        )
        peer_session = PeerSession.as_peer(
            cert_path=peer_cert,
            key_path=peer_key,
            state=empty_state,
            recv_dir=recv_dir,
        )

        async def run_source() -> str:
            return await source_session.run(
                dialer_reader,
                dialer_writer,
                dialer_endpoint.protocol._quic,
            )

        async def run_peer() -> str:
            peer_reader, peer_writer = await asyncio.wait_for(listener_stream, timeout=5.0)
            return await peer_session.run(
                peer_reader,
                peer_writer,
                accepted_protocol._quic,
            )

        results = await asyncio.gather(
            run_source(),
            run_peer(),
            return_exceptions=True,
        )
        # Both sides should have raised PeerAuthError on the peer fingerprint check.
        assert any(isinstance(r, PeerAuthError) for r in results)
    finally:
        if dialer_endpoint is not None:
            with contextlib.suppress(Exception):
                dialer_endpoint.transport.close()
        if listener_endpoint is not None:
            with contextlib.suppress(Exception):
                listener_endpoint.transport.close()
        dialer_sock.close()
        listener_sock.close()


async def test_backup_direction_violation_still_fires(tmp_path: Path) -> None:
    """A PEER session that tries to send_files must raise DirectionViolationError."""
    # The legacy direction policy is reused by PeerSession unchanged. This test
    # documents that the policy still trips when the wrong side calls the wrong
    # method — it does not need a QUIC connection.
    session = BackupSession.as_peer()
    with pytest.raises(DirectionViolationError):
        # The writer/files are irrelevant; the violation is raised before any I/O.
        await session.send_files(None, None, files=(tmp_path / "x",), root=tmp_path)  # type: ignore[arg-type]
