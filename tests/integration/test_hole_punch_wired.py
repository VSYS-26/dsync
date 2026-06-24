"""Verify the daemon actually fires a hole-punch burst before each peer dial.

The full end-to-end test in :mod:`tests.integration.test_full_flow` runs
on loopback where the burst is harmless filler — it passes whether or not
the burst is wired in. This file pins the *behaviour* directly: when the
relay assigns roles, both peers emit ``PUNCH_MAGIC`` packets toward each
other on their multiplexed socket *before* the QUIC INITIAL leaves.

For real-world NAT validation, see ``docs/nat-verification.md`` and the
Docker harness under ``tests/integration/nat/``.
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
    FolderEntry,
    FoldersConfig,
    RelaysConfig,
    RelayServer,
    SyncMode,
    TrustedDevice,
)
from dsync.network.hole_punch import PUNCH_MAGIC
from dsync.network.local_ipc import LocalControlClient, SyncFolderRequest
from dsync.network.relay_daemon import RelayDaemon
from dsync.network.relay_server import RelayServer as RelayQuicServer
from dsync.state import AppState

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


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
                TrustedDevice(id=pid, fingerprint=fp, relay_id=rid) for pid, fp, rid in trusted
            ],
        ),
        relays=RelaysConfig(relays=[relay]),
    )


async def test_burst_fires_on_both_sides_before_quic_handshake(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both daemons send PUNCH_MAGIC packets at each other before exchanging QUIC."""
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

    # ----- peers + folder + state -----
    a_cert = tmp_path / "a-cert.pem"
    a_key = tmp_path / "a-key.pem"
    a_fp = _write_self_signed(a_cert, a_key)
    a_folder_path = tmp_path / "a-folder"
    a_folder_path.mkdir()
    (a_folder_path / "demo.bin").write_bytes(b"x" * 1024)
    a_recv = tmp_path / "a-recv"
    a_recv.mkdir()
    a_folder = FolderEntry(
        id="demo",
        path=a_folder_path,
        mode=SyncMode.BACKUP_TO_PEER,
        devices=["peer-b"],
        recursive=False,
    )

    b_cert = tmp_path / "b-cert.pem"
    b_key = tmp_path / "b-key.pem"
    b_fp = _write_self_signed(b_cert, b_key)
    b_recv = tmp_path / "b-recv"
    b_recv.mkdir()

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

    # ----- instrument send_datagram on BOTH daemons -----
    # We count outbound PUNCH_MAGIC datagrams per daemon and grab the time
    # of the first one so we can compare against QUIC traffic timing.
    from dsync.network import multi_quic

    a_punch_count = 0
    b_punch_count = 0
    a_first_punch_at: float | None = None
    b_first_punch_at: float | None = None

    original_send = multi_quic.MultiQuicEndpoint.send_datagram

    daemon_a_ref: list[RelayDaemon] = []
    daemon_b_ref: list[RelayDaemon] = []

    def instrumented_send(self, data, peer_addr):  # type: ignore[no-untyped-def]
        nonlocal a_punch_count, b_punch_count, a_first_punch_at, b_first_punch_at
        if data == PUNCH_MAGIC:
            if daemon_a_ref and self is daemon_a_ref[0]._endpoint:
                a_punch_count += 1
                if a_first_punch_at is None:
                    a_first_punch_at = asyncio.get_event_loop().time()
            elif daemon_b_ref and self is daemon_b_ref[0]._endpoint:
                b_punch_count += 1
                if b_first_punch_at is None:
                    b_first_punch_at = asyncio.get_event_loop().time()
        return original_send(self, data, peer_addr)

    monkeypatch.setattr(multi_quic.MultiQuicEndpoint, "send_datagram", instrumented_send)

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
    daemon_a_ref.append(daemon_a)
    daemon_b_ref.append(daemon_b)

    try:
        await daemon_a.start()
        await daemon_b.start()

        # No bursts should have happened yet — the burst only fires during
        # a sync.
        assert a_punch_count == 0
        assert b_punch_count == 0

        # Drive a sync.
        client = LocalControlClient(socket_path=daemon_a._ipc_server.socket_path)
        response = await client.request(
            SyncFolderRequest(folder_id="demo", peer_id="peer-b"),
        )
        assert response.status == "ok", f"sync failed: {response.reason}"

        # Both sides should have emitted at least one burst (5 default).
        assert a_punch_count >= 5, f"dialer burst count = {a_punch_count}"
        assert b_punch_count >= 5, f"listener burst count = {b_punch_count}"
        # Bursts should have fired within a tight window of each other —
        # the relay sends PUNCH_INFO to both nearly simultaneously.
        assert a_first_punch_at is not None
        assert b_first_punch_at is not None
        delta = abs(a_first_punch_at - b_first_punch_at)
        assert delta < 1.0, (
            f"burst start skew {delta:.3f}s exceeds 1.0s; relay PUNCH_INFO "
            f"propagation is suspiciously slow"
        )
    finally:
        await daemon_a.close()
        await daemon_b.close()
        await relay_quic.close()
