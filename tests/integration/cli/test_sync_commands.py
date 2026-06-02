from pathlib import Path
import time
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from dsync.cli import cli
from dsync.identity import PeerMapStore

_FP_A = "hex-" + "a" * 64
_FP_B = "hex-" + "b" * 64


def _invoke(runner: CliRunner, config_dir: Path, *args: str):
    return runner.invoke(cli, ["--config-dir", str(config_dir), *args])


def _add_device(runner: CliRunner, config_dir: Path, dev_id: str, fp: str) -> None:
    _invoke(runner, config_dir, "device", "add", dev_id, fp)


def _add_folder(
    runner: CliRunner,
    config_dir: Path,
    folder_id: str,
    path: str,
    mode: str,
    device: str | None = None,
) -> None:
    args = ["folder", "add", folder_id, path, "--mode", mode]
    if device:
        args += ["--device", device]
    _invoke(runner, config_dir, *args)


def _peer_map(tmp_path: Path, fp: str, ip: str = "127.0.0.1") -> Path:
    map_file = tmp_path / "peer-map.json"
    store = PeerMapStore(file_path=map_file, ttl_seconds=3600, now_fn=time.time)
    store.upsert_peer(fp, ip)
    return map_file


def _mock_p2p_node(target: str):
    mock_node = MagicMock()
    mock_node.start = AsyncMock()
    mock_cls = MagicMock(return_value=mock_node)
    return patch(target, mock_cls), mock_cls, mock_node


# ── sync start ────────────────────────────────────────────────────────────────


def test_sync_start_client_mode(
    cli_runner: CliRunner, tmp_config_dir: Path, tmp_path: Path
) -> None:
    patcher, mock_cls, _mock_node = _mock_p2p_node("dsync.cli.commands.sync.start.P2PNode")
    cert = str(tmp_path / "cert.pem")
    key = str(tmp_path / "key.pem")
    (tmp_path / "cert.pem").write_text("dummy")
    (tmp_path / "key.pem").write_text("dummy")

    with patcher:
        result = _invoke(
            cli_runner,
            tmp_config_dir,
            "sync",
            "start",
            "--mode",
            "client",
            "--cert",
            cert,
            "--key",
            key,
        )

    assert result.exit_code == 0
    mock_cls.assert_called_once()
    assert mock_cls.call_args.args[0] is False  # is_server=False


def test_sync_start_server_mode(
    cli_runner: CliRunner, tmp_config_dir: Path, tmp_path: Path
) -> None:
    patcher, mock_cls, _mock_node = _mock_p2p_node("dsync.cli.commands.sync.start.P2PNode")
    cert = str(tmp_path / "cert.pem")
    key = str(tmp_path / "key.pem")
    (tmp_path / "cert.pem").write_text("dummy")
    (tmp_path / "key.pem").write_text("dummy")

    with patcher:
        result = _invoke(
            cli_runner,
            tmp_config_dir,
            "sync",
            "start",
            "--mode",
            "server",
            "--cert",
            cert,
            "--key",
            key,
        )

    assert result.exit_code == 0
    assert mock_cls.call_args.args[0] is True  # is_server=True


# ── sync run ──────────────────────────────────────────────────────────────────


def test_sync_run_no_folders_exits_nonzero(
    cli_runner: CliRunner, tmp_config_dir: Path, tmp_path: Path
) -> None:
    result = _invoke(
        cli_runner, tmp_config_dir, "sync", "run", "--map-file", str(tmp_path / "map.json")
    )
    assert result.exit_code != 0


def test_sync_run_no_devices_exits_nonzero(
    cli_runner: CliRunner, tmp_config_dir: Path, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _add_folder(cli_runner, tmp_config_dir, "f1", str(src), "mirror")
    result = _invoke(
        cli_runner, tmp_config_dir, "sync", "run", "--map-file", str(tmp_path / "map.json")
    )
    assert result.exit_code != 0


def test_sync_run_empty_peer_map_exits_nonzero(
    cli_runner: CliRunner, tmp_config_dir: Path, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _add_device(cli_runner, tmp_config_dir, "dev-a", _FP_A)
    _add_folder(cli_runner, tmp_config_dir, "f1", str(src), "mirror", device="dev-a")
    empty_map = tmp_path / "map.json"
    empty_map.write_text('{"peers": {}}')
    result = _invoke(cli_runner, tmp_config_dir, "sync", "run", "--map-file", str(empty_map))
    assert result.exit_code != 0


def test_sync_run_skips_backup_from_peer_folder(
    cli_runner: CliRunner, tmp_config_dir: Path, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _add_device(cli_runner, tmp_config_dir, "dev-a", _FP_A)
    _add_folder(cli_runner, tmp_config_dir, "f1", str(src), "backup-from-peer", device="dev-a")
    map_file = _peer_map(tmp_path, _FP_A)

    patcher, mock_cls, _ = _mock_p2p_node("dsync.cli.commands.sync.run_backup.P2PNode")
    with patcher:
        result = _invoke(cli_runner, tmp_config_dir, "sync", "run", "--map-file", str(map_file))

    assert result.exit_code == 0
    mock_cls.assert_not_called()


def test_sync_run_unknown_folder_id_exits_nonzero(
    cli_runner: CliRunner, tmp_config_dir: Path, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _add_device(cli_runner, tmp_config_dir, "dev-a", _FP_A)
    _add_folder(cli_runner, tmp_config_dir, "f1", str(src), "mirror", device="dev-a")
    map_file = _peer_map(tmp_path, _FP_A)

    patcher, _, _ = _mock_p2p_node("dsync.cli.commands.sync.run_backup.P2PNode")
    with patcher:
        result = _invoke(
            cli_runner,
            tmp_config_dir,
            "sync",
            "run",
            "--folder-id",
            "nonexistent",
            "--map-file",
            str(map_file),
        )

    assert result.exit_code != 0


def test_sync_run_success_calls_node_start(
    cli_runner: CliRunner, tmp_config_dir: Path, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _add_device(cli_runner, tmp_config_dir, "dev-a", _FP_A)
    _add_folder(cli_runner, tmp_config_dir, "f1", str(src), "mirror", device="dev-a")
    map_file = _peer_map(tmp_path, _FP_A)

    patcher, mock_cls, mock_node = _mock_p2p_node("dsync.cli.commands.sync.run_backup.P2PNode")
    with patcher:
        result = _invoke(cli_runner, tmp_config_dir, "sync", "run", "--map-file", str(map_file))

    assert result.exit_code == 0
    mock_cls.assert_called_once()
    mock_node.start.assert_called_once()


def test_sync_run_specific_folder_id(
    cli_runner: CliRunner, tmp_config_dir: Path, tmp_path: Path
) -> None:
    src_a = tmp_path / "src_a"
    src_b = tmp_path / "src_b"
    src_a.mkdir()
    src_b.mkdir()
    _add_device(cli_runner, tmp_config_dir, "dev-a", _FP_A)
    _add_device(cli_runner, tmp_config_dir, "dev-b", _FP_B)
    _add_folder(cli_runner, tmp_config_dir, "folder-a", str(src_a), "mirror", device="dev-a")
    _add_folder(cli_runner, tmp_config_dir, "folder-b", str(src_b), "mirror", device="dev-b")
    map_file = _peer_map(tmp_path, _FP_A)

    patcher, mock_cls, mock_node = _mock_p2p_node("dsync.cli.commands.sync.run_backup.P2PNode")
    with patcher:
        result = _invoke(
            cli_runner,
            tmp_config_dir,
            "sync",
            "run",
            "--folder-id",
            "folder-a",
            "--map-file",
            str(map_file),
        )

    assert result.exit_code == 0
    assert mock_cls.call_count == 1
    assert mock_node.start.call_count == 1


def test_sync_run_connection_refused_shows_failed_count(
    cli_runner: CliRunner, tmp_config_dir: Path, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _add_device(cli_runner, tmp_config_dir, "dev-a", _FP_A)
    _add_folder(cli_runner, tmp_config_dir, "f1", str(src), "mirror", device="dev-a")
    map_file = _peer_map(tmp_path, _FP_A)

    patcher, _mock_cls, mock_node = _mock_p2p_node("dsync.cli.commands.sync.run_backup.P2PNode")
    mock_node.start.side_effect = ConnectionRefusedError
    with patcher:
        result = _invoke(cli_runner, tmp_config_dir, "sync", "run", "--map-file", str(map_file))

    assert result.exit_code == 0
    assert "Failed" in result.output


def test_sync_run_peer_not_in_map_counts_as_failure(
    cli_runner: CliRunner, tmp_config_dir: Path, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _add_device(cli_runner, tmp_config_dir, "dev-b", _FP_B)
    _add_folder(cli_runner, tmp_config_dir, "f1", str(src), "mirror", device="dev-b")
    map_file = _peer_map(tmp_path, _FP_A)  # only _FP_A in map, not _FP_B

    patcher, _mock_cls, _mock_node = _mock_p2p_node("dsync.cli.commands.sync.run_backup.P2PNode")
    with patcher:
        result = _invoke(cli_runner, tmp_config_dir, "sync", "run", "--map-file", str(map_file))

    assert result.exit_code == 0
    assert "Failed" in result.output
