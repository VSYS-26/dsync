from pathlib import Path
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
import pytest
import typer

from dsync.cli.commands.sync.start import _discover_peer_by_id, _get_own_fingerprint
from dsync.config import DaemonConfig, DevicesConfig, FoldersConfig, TrustedDevice
from dsync.network.discovery import DiscoveryStats
from dsync.state import AppState

_START = "dsync.cli.commands.sync.start"


def _state(tmp_path: Path, devices=()) -> AppState:
    return AppState(
        config_dir=tmp_path,
        folders=FoldersConfig(entries=[]),
        devices=DevicesConfig(trusted_devices=list(devices)),
        daemon=DaemonConfig(),
    )


# ── _get_own_fingerprint ──────────────────────────────────────────────────────


def test_get_own_fingerprint_returns_hex_digest(tmp_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path = tmp_path / "key.pem"
    key_path.write_bytes(
        key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption())
    )

    fp = _get_own_fingerprint("unused-cert.pem", str(key_path))

    assert fp is not None
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


def test_get_own_fingerprint_returns_none_for_missing_key(tmp_path: Path) -> None:
    fp = _get_own_fingerprint("cert.pem", str(tmp_path / "nonexistent.pem"))
    assert fp is None


# ── _discover_peer_by_id ──────────────────────────────────────────────────────


def test_discover_peer_by_id_no_device_id_returns_localhost(tmp_path: Path) -> None:
    state = _state(tmp_path)
    assert _discover_peer_by_id(state, None) == "127.0.0.1"


def test_discover_peer_by_id_unknown_device_raises(tmp_path: Path) -> None:
    state = _state(tmp_path)
    with pytest.raises(typer.Exit):
        _discover_peer_by_id(state, "ghost")


def test_discover_peer_by_id_found_returns_ip(tmp_path: Path) -> None:
    fp = "a" * 64
    state = _state(tmp_path, devices=[TrustedDevice(id="dev-a", fingerprint=fp)])
    fake_peer = MagicMock(ipv4="10.0.0.5")

    with (
        patch(f"{_START}._get_own_fingerprint", return_value="b" * 64),
        patch(f"{_START}.FingerprintAnnouncer") as mock_announcer_cls,
        patch(f"{_START}.PeerDiscoveryRunner") as mock_runner_cls,
    ):
        mock_runner_cls.return_value.discover.return_value = (
            {fp: fake_peer},
            DiscoveryStats(events_seen=1, peers_written=1),
        )
        result = _discover_peer_by_id(state, "dev-a")

    assert result == "10.0.0.5"
    mock_announcer_cls.return_value.start.assert_called_once()
    mock_announcer_cls.return_value.stop.assert_called_once()


def test_discover_peer_by_id_not_found_raises(tmp_path: Path) -> None:
    fp = "a" * 64
    state = _state(tmp_path, devices=[TrustedDevice(id="dev-a", fingerprint=fp)])

    with (
        patch(f"{_START}._get_own_fingerprint", return_value=None),
        patch(f"{_START}.FingerprintAnnouncer"),
        patch(f"{_START}.PeerDiscoveryRunner") as mock_runner_cls,
    ):
        mock_runner_cls.return_value.discover.return_value = ({}, DiscoveryStats())
        with pytest.raises(typer.Exit):
            _discover_peer_by_id(state, "dev-a")
