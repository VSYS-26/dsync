from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from dsync.cli import cli
from dsync.config import DaemonConfig, SchedulerConfig


def _invoke(runner: CliRunner, config_dir: Path, *args: str):
    return runner.invoke(cli, ["--config-dir", str(config_dir), *args])


def _mock_daemon(*, is_enabled: bool = False, is_running: bool = False) -> MagicMock:
    daemon = MagicMock()
    daemon.is_enabled.return_value = is_enabled
    daemon.is_running.return_value = is_running
    return daemon


# ── server enable/disable/status ────────────────────────────────────────────


def test_server_enable_success_persists_config(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    mock_daemon = _mock_daemon(is_enabled=False)
    with patch("dsync.cli.commands.server.enable.ServerDaemon", return_value=mock_daemon):
        result = _invoke(
            cli_runner,
            tmp_config_dir,
            "server",
            "enable",
            "--port",
            "1234",
            "--cert",
            "c.pem",
            "--key",
            "k.pem",
        )

    assert result.exit_code == 0
    mock_daemon.enable.assert_called_once()
    cfg = DaemonConfig.load(tmp_config_dir)
    assert cfg.enabled is True
    assert cfg.port == 1234


def test_server_enable_already_enabled_is_noop(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    mock_daemon = _mock_daemon(is_enabled=True)
    with patch("dsync.cli.commands.server.enable.ServerDaemon", return_value=mock_daemon):
        result = _invoke(cli_runner, tmp_config_dir, "server", "enable")

    assert result.exit_code == 0
    mock_daemon.enable.assert_not_called()


def test_server_disable_success_persists_config(
    cli_runner: CliRunner, tmp_config_dir: Path
) -> None:
    mock_daemon = _mock_daemon(is_enabled=True)
    with patch("dsync.cli.commands.server.disable.ServerDaemon", return_value=mock_daemon):
        result = _invoke(cli_runner, tmp_config_dir, "server", "disable")

    assert result.exit_code == 0
    mock_daemon.disable.assert_called_once()
    cfg = DaemonConfig.load(tmp_config_dir)
    assert cfg.enabled is False


def test_server_disable_not_enabled_is_noop(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    mock_daemon = _mock_daemon(is_enabled=False)
    with patch("dsync.cli.commands.server.disable.ServerDaemon", return_value=mock_daemon):
        result = _invoke(cli_runner, tmp_config_dir, "server", "disable")

    assert result.exit_code == 0
    mock_daemon.disable.assert_not_called()


def test_server_status_enabled_and_running_shows_port(
    cli_runner: CliRunner, tmp_config_dir: Path
) -> None:
    mock_daemon = _mock_daemon(is_enabled=True, is_running=True)
    with patch("dsync.cli.commands.server.status.ServerDaemon", return_value=mock_daemon):
        result = _invoke(cli_runner, tmp_config_dir, "server", "status")

    assert result.exit_code == 0
    assert "Enabled" in result.output
    assert "Running" in result.output
    assert "Port:" in result.output


def test_server_status_disabled_hides_extra_info(
    cli_runner: CliRunner, tmp_config_dir: Path
) -> None:
    mock_daemon = _mock_daemon(is_enabled=False)
    with patch("dsync.cli.commands.server.status.ServerDaemon", return_value=mock_daemon):
        result = _invoke(cli_runner, tmp_config_dir, "server", "status")

    assert result.exit_code == 0
    assert "Disabled" in result.output
    assert "Port:" not in result.output


# ── scheduler enable/disable/status/run ─────────────────────────────────────


def test_scheduler_enable_success_persists_config(
    cli_runner: CliRunner, tmp_config_dir: Path
) -> None:
    mock_daemon = _mock_daemon(is_enabled=False)
    with patch("dsync.cli.commands.scheduler.enable.SchedulerDaemon", return_value=mock_daemon):
        result = _invoke(
            cli_runner,
            tmp_config_dir,
            "scheduler",
            "enable",
            "--cert",
            "c.pem",
            "--key",
            "k.pem",
        )

    assert result.exit_code == 0
    mock_daemon.enable.assert_called_once()
    cfg = SchedulerConfig.load(tmp_config_dir)
    assert cfg.enabled is True


def test_scheduler_disable_success_persists_config(
    cli_runner: CliRunner, tmp_config_dir: Path
) -> None:
    mock_daemon = _mock_daemon(is_enabled=True)
    with patch("dsync.cli.commands.scheduler.disable.SchedulerDaemon", return_value=mock_daemon):
        result = _invoke(cli_runner, tmp_config_dir, "scheduler", "disable")

    assert result.exit_code == 0
    mock_daemon.disable.assert_called_once()
    cfg = SchedulerConfig.load(tmp_config_dir)
    assert cfg.enabled is False


def test_scheduler_status_enabled_shows_config_dir(
    cli_runner: CliRunner, tmp_config_dir: Path
) -> None:
    mock_daemon = _mock_daemon(is_enabled=True, is_running=True)
    with patch("dsync.cli.commands.scheduler.status.SchedulerDaemon", return_value=mock_daemon):
        result = _invoke(cli_runner, tmp_config_dir, "scheduler", "status")

    assert result.exit_code == 0
    assert "Enabled" in result.output
    assert "Config Dir:" in result.output


def test_scheduler_status_disabled_hides_extra_info(
    cli_runner: CliRunner, tmp_config_dir: Path
) -> None:
    mock_daemon = _mock_daemon(is_enabled=False)
    with patch("dsync.cli.commands.scheduler.status.SchedulerDaemon", return_value=mock_daemon):
        result = _invoke(cli_runner, tmp_config_dir, "scheduler", "status")

    assert result.exit_code == 0
    assert "Disabled" in result.output
    assert "Config Dir:" not in result.output


def test_scheduler_run_invokes_runner(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    mock_runner = MagicMock()
    mock_runner.run = AsyncMock()
    with patch(
        "dsync.cli.commands.scheduler.run.SchedulerRunner", return_value=mock_runner
    ) as mock_cls:
        result = _invoke(
            cli_runner, tmp_config_dir, "scheduler", "run", "--cert", "c.pem", "--key", "k.pem"
        )

    assert result.exit_code == 0
    mock_cls.assert_called_once()
    mock_runner.run.assert_awaited_once()
