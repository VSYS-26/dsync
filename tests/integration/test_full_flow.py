"""End-to-end test: relay + 2 daemons + IPC sync request → file delivered.

Spins up an in-process ``RelayServer`` on ``127.0.0.1:0``, two
``RelayDaemon`` instances (one source, one target), each connected to the
relay. Then a ``LocalControlClient`` sends a ``sync_folder`` request to
the source daemon, the relay brokers the matchmaking, the two daemons
open a direct QUIC connection between themselves (over the SAME UDP
socket each daemon uses to talk to the relay — the multiplexing claim),
and the file lands in the target daemon's ``recv_dir``.

This is the first test that exercises the FULL new architecture; if it
passes, we know the wire protocol, the daemon, the IPC, the multiplexing,
and the PeerSession all compose correctly.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import os
from typing import TYPE_CHECKING

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from dsync.config import (
    DevicesConfig,
    FolderEntry,
    FoldersConfig,
    RelayServer,
    RelaysConfig,
    SyncMode,
    TrustedDevice,
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
    folder: FolderEntry | None,
    trusted: list[tuple[str, str, str]],
) -> AppState:
    return AppState(
        config_dir=config_dir,
        folders=FoldersConfig(entries=[folder] if folder is not None else []),
        devices=DevicesConfig(
            trusted_devices=[
                TrustedDevice(id=pid, fingerprint=fp, relay_id=rid)
                for pid, fp, rid in trusted
            ],
        ),
        relays=RelaysConfig(relays=[relay]),
    )


async def test_full_flow_relay_two_daemons_and_run_backup(
    tmp_path: Path,
) -> None:
    """File flows from source daemon to peer daemon via the relay."""
    # ----- relay -----
    relay_cert = tmp_path / "relay-cert.pem"
    relay_key = tmp_path / "relay-key.pem"
    relay_fp = _write_self_signed(relay_cert, relay_key)

    relay_quic = RelayQuicServer(
        host="127.0.0.1",
        port=0,
        cert_path=relay_cert,
        key_path=relay_key,
    )
    await relay_quic.start()

    relay_entry = RelayServer(
        id="relay-test",
        host="127.0.0.1",
        port=relay_quic.bound_port,
        fingerprint=relay_fp,
    )

    # ----- peer A (source) -----
    a_cert = tmp_path / "a-cert.pem"
    a_key = tmp_path / "a-key.pem"
    a_fp = _write_self_signed(a_cert, a_key)
    a_folder_path = tmp_path / "a-folder"
    a_folder_path.mkdir()
    payload = b"e2e-relay-daemon-test\n" * 2048  # ~44 KB
    (a_folder_path / "demo.bin").write_bytes(payload)
    expected_digest = hashlib.sha256(payload).hexdigest()
    a_recv = tmp_path / "a-recv"
    a_recv.mkdir()
    a_folder = FolderEntry(
        id="demo",
        path=a_folder_path,
        mode=SyncMode.BACKUP_TO_PEER,
        devices=["peer-b"],
        recursive=False,
    )

    # ----- peer B (target) -----
    b_cert = tmp_path / "b-cert.pem"
    b_key = tmp_path / "b-key.pem"
    b_fp = _write_self_signed(b_cert, b_key)
    b_recv = tmp_path / "b-recv"
    b_recv.mkdir()

    # ----- AppStates -----
    a_state = _build_state(
        config_dir=tmp_path,
        relay=relay_entry,
        folder=a_folder,
        trusted=[("peer-b", b_fp, "relay-test")],
    )
    b_state = _build_state(
        config_dir=tmp_path,
        relay=relay_entry,
        folder=None,
        trusted=[("peer-a", a_fp, "relay-test")],
    )

    # ----- daemons -----
    ipc_dir = tmp_path / "ipc"
    ipc_dir.mkdir()
    daemon_a = RelayDaemon(
        relay=relay_entry,
        cert_path=a_cert,
        key_path=a_key,
        state=a_state,
        recv_dir=a_recv,
        ipc_socket_path=ipc_dir / f"a-{os.getpid()}.sock",
    )
    daemon_b = RelayDaemon(
        relay=relay_entry,
        cert_path=b_cert,
        key_path=b_key,
        state=b_state,
        recv_dir=b_recv,
        ipc_socket_path=ipc_dir / f"b-{os.getpid()}.sock",
    )

    try:
        await daemon_a.start()
        await daemon_b.start()

        # ----- trigger sync via IPC -----
        client = LocalControlClient(socket_path=daemon_a._ipc_server.socket_path)
        response = await client.request(
            SyncFolderRequest(folder_id="demo", peer_id="peer-b"),
        )
        assert response.status == "ok", f"sync failed: {response.reason}"

        # Allow B's receive task to finish writing the file before we assert.
        for _ in range(50):
            received = b_recv / "peer-a" / "demo.bin"
            if received.is_file() and received.stat().st_size == len(payload):
                break
            await asyncio.sleep(0.05)

        received = b_recv / "peer-a" / "demo.bin"
        assert received.is_file()
        assert hashlib.sha256(received.read_bytes()).hexdigest() == expected_digest
    finally:
        await daemon_a.close()
        await daemon_b.close()
        await relay_quic.close()


async def test_sync_to_unknown_peer_returns_error(tmp_path: Path) -> None:
    """A sync request for a peer not in devices.yaml gets a clear error."""
    relay_cert = tmp_path / "relay-cert.pem"
    relay_key = tmp_path / "relay-key.pem"
    relay_fp = _write_self_signed(relay_cert, relay_key)
    relay_quic = RelayQuicServer(
        host="127.0.0.1", port=0, cert_path=relay_cert, key_path=relay_key,
    )
    await relay_quic.start()
    relay_entry = RelayServer(
        id="relay-test", host="127.0.0.1", port=relay_quic.bound_port, fingerprint=relay_fp,
    )

    a_cert = tmp_path / "a-cert.pem"
    a_key = tmp_path / "a-key.pem"
    _write_self_signed(a_cert, a_key)
    a_state = _build_state(
        config_dir=tmp_path,
        relay=relay_entry,
        folder=None,
        trusted=[],
    )
    a_recv = tmp_path / "a-recv"
    a_recv.mkdir()

    daemon = RelayDaemon(
        relay=relay_entry,
        cert_path=a_cert,
        key_path=a_key,
        state=a_state,
        recv_dir=a_recv,
        ipc_socket_path=tmp_path / f"a-{os.getpid()}.sock",
    )
    try:
        await daemon.start()
        client = LocalControlClient(socket_path=daemon._ipc_server.socket_path)
        response = await client.request(
            SyncFolderRequest(folder_id="ghost", peer_id="nobody"),
        )
        assert response.status == "error"
        assert response.reason is not None
        assert "unknown peer" in response.reason
    finally:
        await daemon.close()
        await relay_quic.close()
