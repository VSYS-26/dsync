import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_der_public_key,
)
import pytest

from dsync.config import (
    DaemonConfig,
    DevicesConfig,
    FolderEntry,
    FoldersConfig,
    SyncMode,
    TrustedDevice,
)
from dsync.network.errors import PeerAuthError
from dsync.network.node import P2PNode
from dsync.state import AppState


def _make_rsa_key(tmp_path: Path, name: str, key_size: int = 2048) -> tuple[Path, bytes, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    key_path = tmp_path / f"{name}.pem"
    key_path.write_bytes(
        key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption())
    )
    spki = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    fingerprint = hashlib.sha256(spki).hexdigest()
    return key_path, spki, fingerprint


def _state(tmp_path: Path, folders=(), devices=()) -> AppState:
    return AppState(
        config_dir=tmp_path,
        folders=FoldersConfig(entries=list(folders)),
        devices=DevicesConfig(trusted_devices=list(devices)),
        daemon=DaemonConfig(),
    )


# ── __init__ key validation ───────────────────────────────────────────────────


def test_init_rejects_non_rsa_key(tmp_path: Path) -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    key_path = tmp_path / "key.pem"
    key_path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))

    with pytest.raises(TypeError, match="RSA"):
        P2PNode(True, "cert.pem", str(key_path), _state(tmp_path))


def test_init_rejects_non_2048_rsa_key(tmp_path: Path) -> None:
    key_path, _, _ = _make_rsa_key(tmp_path, "key", key_size=3072)

    with pytest.raises(TypeError, match="2048"):
        P2PNode(True, "cert.pem", str(key_path), _state(tmp_path))


def test_init_sets_own_device_id_from_trusted_devices(tmp_path: Path) -> None:
    key_path, _, fp = _make_rsa_key(tmp_path, "key")
    state = _state(tmp_path, devices=[TrustedDevice(id="me", fingerprint=fp)])

    node = P2PNode(True, "cert.pem", str(key_path), state)

    assert node._own_device_id == "me"


def test_init_falls_back_to_fingerprint_when_untrusted(tmp_path: Path) -> None:
    key_path, _, fp = _make_rsa_key(tmp_path, "key")

    node = P2PNode(True, "cert.pem", str(key_path), _state(tmp_path))

    assert node._own_device_id == fp


# ── channel-binding sign/verify ───────────────────────────────────────────────


def test_sign_and_verify_roundtrip(tmp_path: Path) -> None:
    key_path, spki, _ = _make_rsa_key(tmp_path, "key")
    node = P2PNode(True, "cert.pem", str(key_path), _state(tmp_path))
    binding = b"x" * 32

    sig = node._sign_channel_binding(binding)
    pub_key = load_der_public_key(spki)

    P2PNode._verify_peer_signature(pub_key, binding, sig)  # does not raise


def test_verify_rejects_signature_over_wrong_binding(tmp_path: Path) -> None:
    key_path, spki, _ = _make_rsa_key(tmp_path, "key")
    node = P2PNode(True, "cert.pem", str(key_path), _state(tmp_path))
    sig = node._sign_channel_binding(b"x" * 32)
    pub_key = load_der_public_key(spki)

    with pytest.raises(ValueError, match="Peer signature invalid"):
        P2PNode._verify_peer_signature(pub_key, b"y" * 32, sig)


# ── auth message packing ──────────────────────────────────────────────────────


def test_pack_and_unpack_auth_msg_roundtrip() -> None:
    spki = b"s" * 294
    sig = b"g" * 256

    packed = P2PNode._pack_auth_msg(spki, sig)
    unpacked_spki, unpacked_sig = P2PNode._unpack_auth_msg(packed)

    assert unpacked_spki == spki
    assert unpacked_sig == sig


# ── full handshake + sync (TLS channel binding mocked) ────────────────────────


def _node_pair(
    tmp_path: Path, *, client_mode: SyncMode, server_mode: SyncMode
) -> tuple[P2PNode, P2PNode, Path]:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "file.txt").write_bytes(b"hello sync")

    client_key, _, client_fp = _make_rsa_key(tmp_path, "client")
    server_key, _, server_fp = _make_rsa_key(tmp_path, "server")

    client_folder = FolderEntry(id="docs", path=src, mode=client_mode)
    server_folder = FolderEntry(id="docs", path=dst, mode=server_mode)

    client_state = _state(
        tmp_path,
        folders=[client_folder],
        devices=[TrustedDevice(id="server", fingerprint=server_fp)],
    )
    server_state = _state(
        tmp_path,
        folders=[server_folder],
        devices=[TrustedDevice(id="client", fingerprint=client_fp)],
    )

    client = P2PNode(False, "client-cert.pem", str(client_key), client_state, folder=client_folder)
    server = P2PNode(True, "server-cert.pem", str(server_key), server_state)
    return client, server, dst


async def test_handle_secure_connection_full_sync_succeeds(stream_pair, tmp_path: Path) -> None:
    (reader_a, writer_a), (reader_b, writer_b) = stream_pair
    client, server, dst = _node_pair(
        tmp_path, client_mode=SyncMode.MIRROR, server_mode=SyncMode.MIRROR
    )

    with patch("dsync.network.node.get_tls_channel_binding", return_value=b"x" * 32):
        await asyncio.gather(
            client.handle_secure_connection(reader_a, writer_a),
            server.handle_secure_connection(reader_b, writer_b),
        )

    assert (dst / "file.txt").read_bytes() == b"hello sync"


async def test_handle_secure_connection_rejects_unknown_fingerprint(
    stream_pair, tmp_path: Path
) -> None:
    (reader_a, writer_a), (reader_b, writer_b) = stream_pair
    client, server, dst = _node_pair(
        tmp_path, client_mode=SyncMode.MIRROR, server_mode=SyncMode.MIRROR
    )
    server.trusted_devices = {}

    with patch("dsync.network.node.get_tls_channel_binding", return_value=b"x" * 32):
        await asyncio.gather(
            client.handle_secure_connection(reader_a, writer_a),
            server.handle_secure_connection(reader_b, writer_b),
        )

    assert writer_a.is_closing()
    assert writer_b.is_closing()
    assert not (dst / "file.txt").exists()


async def test_handle_secure_connection_rejects_path_unsafe_peer_id(
    stream_pair, tmp_path: Path
) -> None:
    (reader_a, writer_a), (reader_b, writer_b) = stream_pair
    client, server, dst = _node_pair(
        tmp_path, client_mode=SyncMode.MIRROR, server_mode=SyncMode.MIRROR
    )
    client_fp = next(iter(server.trusted_devices))
    server.trusted_devices = {client_fp: "../evil"}

    with patch("dsync.network.node.get_tls_channel_binding", return_value=b"x" * 32):
        await asyncio.gather(
            client.handle_secure_connection(reader_a, writer_a),
            server.handle_secure_connection(reader_b, writer_b),
        )

    assert not (dst / "file.txt").exists()


# ── start_sync edge cases (no live handshake needed) ──────────────────────────


async def test_start_sync_server_rejects_multiple_peer_entries(tmp_path: Path) -> None:
    key_path, _, _ = _make_rsa_key(tmp_path, "key")
    server_folder = FolderEntry(id="docs", path=tmp_path / "dst", mode=SyncMode.MIRROR)
    server = P2PNode(True, "cert.pem", str(key_path), _state(tmp_path, folders=[server_folder]))

    peer_config = FoldersConfig(
        entries=[
            FolderEntry(id="a", path=Path("/a"), mode=SyncMode.MIRROR),
            FolderEntry(id="b", path=Path("/b"), mode=SyncMode.MIRROR),
        ]
    )
    with patch("dsync.network.node.ConfigExchange") as mock_cls:
        mock_cls.return_value.exchange_and_validate = AsyncMock(return_value=peer_config)
        with pytest.raises(PeerAuthError, match="exactly one folder"):
            await server.start_sync(MagicMock(), MagicMock(), "client")


async def test_start_sync_server_rejects_unconfigured_folder(tmp_path: Path) -> None:
    key_path, _, _ = _make_rsa_key(tmp_path, "key")
    server = P2PNode(True, "cert.pem", str(key_path), _state(tmp_path, folders=[]))

    peer_config = FoldersConfig(
        entries=[FolderEntry(id="docs", path=Path("/x"), mode=SyncMode.MIRROR)]
    )
    with patch("dsync.network.node.ConfigExchange") as mock_cls:
        mock_cls.return_value.exchange_and_validate = AsyncMock(return_value=peer_config)
        with pytest.raises(PeerAuthError, match="not configured locally"):
            await server.start_sync(MagicMock(), MagicMock(), "client")


async def test_start_sync_client_requires_folder(tmp_path: Path) -> None:
    key_path, _, _ = _make_rsa_key(tmp_path, "key")
    client = P2PNode(False, "cert.pem", str(key_path), _state(tmp_path), folder=None)

    with pytest.raises(ValueError, match="requires folder"):
        await client.start_sync(MagicMock(), MagicMock(), "server")
