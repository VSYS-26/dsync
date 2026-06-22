from pathlib import Path
import time
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from dsync.cli import cli
from dsync.crypto.keys import generate_keypair
from dsync.identity import DiscoveredPeer, PeerMapStore
from dsync.network.discovery import DiscoveryStats

_FP_A = "hex-" + "a" * 64


def _invoke(runner: CliRunner, config_dir: Path, *args: str):
    return runner.invoke(cli, ["--config-dir", str(config_dir), *args])


# ── peer map ─────────────────────────────────────────────────────────────────


def test_peer_map_empty(cli_runner: CliRunner, tmp_config_dir: Path, tmp_path: Path) -> None:
    map_file = tmp_path / "peer-map.json"
    result = _invoke(cli_runner, tmp_config_dir, "peer", "map", "--map-file", str(map_file))
    assert result.exit_code == 0
    assert result.output


def test_peer_map_shows_entries(
    cli_runner: CliRunner, tmp_config_dir: Path, tmp_path: Path
) -> None:
    map_file = tmp_path / "peer-map.json"
    store = PeerMapStore(file_path=map_file, ttl_seconds=3600, now_fn=time.time)
    store.upsert_peer(_FP_A, "192.168.1.42")

    result = _invoke(cli_runner, tmp_config_dir, "peer", "map", "--map-file", str(map_file))
    assert result.exit_code == 0
    assert _FP_A in result.output
    assert "192.168.1.42" in result.output


# ── peer announce ─────────────────────────────────────────────────────────────


def test_announce_no_keypair_exits_nonzero(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    with patch("dsync.cli.commands.peer.announce.load_keypair", side_effect=FileNotFoundError):
        result = _invoke(cli_runner, tmp_config_dir, "peer", "announce", "--seconds", "0")
    assert result.exit_code != 0


def test_announce_success_calls_start_stop(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    private_pem, public_pem = generate_keypair()
    mock_announcer = MagicMock()

    with (
        patch(
            "dsync.cli.commands.peer.announce.load_keypair", return_value=(private_pem, public_pem)
        ),
        patch("dsync.cli.commands.peer.announce.FingerprintAnnouncer", return_value=mock_announcer),
    ):
        result = _invoke(cli_runner, tmp_config_dir, "peer", "announce", "--seconds", "0")

    assert result.exit_code == 0
    mock_announcer.start.assert_called_once()
    mock_announcer.stop.assert_called_once()


def test_announce_keyboard_interrupt_stops_cleanly(
    cli_runner: CliRunner, tmp_config_dir: Path
) -> None:
    private_pem, public_pem = generate_keypair()
    mock_announcer = MagicMock()

    with (
        patch(
            "dsync.cli.commands.peer.announce.load_keypair", return_value=(private_pem, public_pem)
        ),
        patch("dsync.cli.commands.peer.announce.FingerprintAnnouncer", return_value=mock_announcer),
        patch("dsync.cli.commands.peer.announce.time.sleep", side_effect=KeyboardInterrupt),
    ):
        result = _invoke(cli_runner, tmp_config_dir, "peer", "announce", "--seconds", "5")

    assert result.exit_code == 0
    mock_announcer.stop.assert_called_once()


def test_announce_passes_fingerprint_to_announcer(
    cli_runner: CliRunner, tmp_config_dir: Path
) -> None:
    private_pem, public_pem = generate_keypair()
    mock_announcer = MagicMock()

    with (
        patch(
            "dsync.cli.commands.peer.announce.load_keypair", return_value=(private_pem, public_pem)
        ),
        patch(
            "dsync.cli.commands.peer.announce.FingerprintAnnouncer", return_value=mock_announcer
        ) as mock_cls,
    ):
        _invoke(cli_runner, tmp_config_dir, "peer", "announce", "--seconds", "0")

    call_kwargs = mock_cls.call_args
    assert "fingerprint" in call_kwargs.kwargs or len(call_kwargs.args) > 0


# ── peer discover ─────────────────────────────────────────────────────────────


def test_discover_no_keypair_warns_but_continues(
    cli_runner: CliRunner, tmp_config_dir: Path, tmp_path: Path
) -> None:
    map_file = tmp_path / "peer-map.json"
    mock_runner = MagicMock()
    mock_runner.discover.return_value = ({}, DiscoveryStats())

    with (
        patch("dsync.cli.commands.peer.discover.load_keypair", side_effect=FileNotFoundError),
        patch("dsync.cli.commands.peer.discover.PeerDiscoveryRunner", return_value=mock_runner),
    ):
        result = _invoke(
            cli_runner,
            tmp_config_dir,
            "peer",
            "discover",
            "--seconds",
            "0",
            "--map-file",
            str(map_file),
        )

    assert result.exit_code == 0
    mock_runner.discover.assert_called_once()


def test_discover_no_peers_found(
    cli_runner: CliRunner, tmp_config_dir: Path, tmp_path: Path
) -> None:
    map_file = tmp_path / "peer-map.json"
    private_pem, public_pem = generate_keypair()
    mock_runner = MagicMock()
    mock_runner.discover.return_value = ({}, DiscoveryStats(events_seen=0, peers_written=0))

    with (
        patch(
            "dsync.cli.commands.peer.discover.load_keypair", return_value=(private_pem, public_pem)
        ),
        patch("dsync.cli.commands.peer.discover.PeerDiscoveryRunner", return_value=mock_runner),
    ):
        result = _invoke(
            cli_runner,
            tmp_config_dir,
            "peer",
            "discover",
            "--seconds",
            "0",
            "--map-file",
            str(map_file),
        )

    assert result.exit_code == 0
    assert "No peers" in result.output


def test_discover_peers_found_shows_them(
    cli_runner: CliRunner, tmp_config_dir: Path, tmp_path: Path
) -> None:
    map_file = tmp_path / "peer-map.json"
    private_pem, public_pem = generate_keypair()
    now = int(time.time())
    peer = DiscoveredPeer(fingerprint=_FP_A, ipv4="10.0.0.5", last_seen=now, expires_at=now + 30)
    mock_runner = MagicMock()
    mock_runner.discover.return_value = (
        {_FP_A: peer},
        DiscoveryStats(events_seen=1, peers_written=1),
    )

    with (
        patch(
            "dsync.cli.commands.peer.discover.load_keypair", return_value=(private_pem, public_pem)
        ),
        patch("dsync.cli.commands.peer.discover.PeerDiscoveryRunner", return_value=mock_runner),
    ):
        result = _invoke(
            cli_runner,
            tmp_config_dir,
            "peer",
            "discover",
            "--seconds",
            "0",
            "--map-file",
            str(map_file),
        )

    assert result.exit_code == 0
    assert "10.0.0.5" in result.output


def test_discover_passes_own_fingerprint_to_runner(
    cli_runner: CliRunner, tmp_config_dir: Path, tmp_path: Path
) -> None:
    map_file = tmp_path / "peer-map.json"
    private_pem, public_pem = generate_keypair()
    mock_runner = MagicMock()
    mock_runner.discover.return_value = ({}, DiscoveryStats())

    with (
        patch(
            "dsync.cli.commands.peer.discover.load_keypair", return_value=(private_pem, public_pem)
        ),
        patch("dsync.cli.commands.peer.discover.PeerDiscoveryRunner", return_value=mock_runner),
    ):
        _invoke(
            cli_runner,
            tmp_config_dir,
            "peer",
            "discover",
            "--seconds",
            "0",
            "--map-file",
            str(map_file),
        )

    call_kwargs = mock_runner.discover.call_args
    assert call_kwargs.kwargs.get("own_fingerprint") is not None or (
        len(call_kwargs.args) > 1 and call_kwargs.args[1] is not None
    )
