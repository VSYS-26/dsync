from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

from dsync.daemon.daemons import SchedulerDaemon, ServerDaemon, _base_argv


def test_base_argv_uses_module_invocation_in_source_checkout(tmp_path: Path) -> None:
    argv = _base_argv(tmp_path)
    assert argv == [sys.executable, "-m", "dsync", "--config-dir", str(tmp_path)]


def test_base_argv_uses_executable_directly_when_frozen(tmp_path: Path) -> None:
    with patch.object(sys, "frozen", True, create=True):
        argv = _base_argv(tmp_path)
    assert argv == [sys.executable, "--config-dir", str(tmp_path)]


def test_server_daemon_identity(tmp_path: Path) -> None:
    daemon = ServerDaemon(tmp_path, port=9999, cert="c.pem", key="k.pem")
    assert daemon.service_name == "dsync-server"
    assert "server" in daemon.description.lower()


def test_server_daemon_command(tmp_path: Path) -> None:
    daemon = ServerDaemon(tmp_path, port=1234, cert="c.pem", key="k.pem")
    command = daemon.command()
    assert "sync" in command
    assert "start" in command
    assert "1234" in command
    assert "c.pem" in command
    assert "k.pem" in command


def test_scheduler_daemon_identity(tmp_path: Path) -> None:
    daemon = SchedulerDaemon(tmp_path, cert="c.pem", key="k.pem")
    assert daemon.service_name == "dsync-scheduler"
    assert "scheduler" in daemon.description.lower()


def test_scheduler_daemon_command(tmp_path: Path) -> None:
    daemon = SchedulerDaemon(tmp_path, cert="c.pem", key="k.pem")
    command = daemon.command()
    assert "scheduler" in command
    assert "run" in command
    assert "c.pem" in command
    assert "k.pem" in command


# ── Daemon base-class wiring (enable/disable/restart/status) ────────────────


def test_log_path_under_config_dir_logs(tmp_path: Path) -> None:
    daemon = ServerDaemon(tmp_path, port=9999, cert="c.pem", key="k.pem")
    assert daemon.log_path == tmp_path / "logs" / "dsync-server.log"


def test_enable_creates_log_dir_and_calls_installer_enable(tmp_path: Path) -> None:
    daemon = ServerDaemon(tmp_path, port=9999, cert="c.pem", key="k.pem")
    mock_installer = MagicMock()
    with patch("dsync.daemon.base.ServiceInstaller.get", return_value=mock_installer):
        daemon.enable()

    assert daemon.log_path.parent.is_dir()
    mock_installer.enable.assert_called_once()


def test_disable_calls_installer_disable(tmp_path: Path) -> None:
    daemon = ServerDaemon(tmp_path, port=9999, cert="c.pem", key="k.pem")
    mock_installer = MagicMock()
    with patch("dsync.daemon.base.ServiceInstaller.get", return_value=mock_installer):
        daemon.disable()

    mock_installer.disable.assert_called_once()


def test_restart_calls_installer_restart(tmp_path: Path) -> None:
    daemon = ServerDaemon(tmp_path, port=9999, cert="c.pem", key="k.pem")
    mock_installer = MagicMock()
    with patch("dsync.daemon.base.ServiceInstaller.get", return_value=mock_installer):
        daemon.restart()

    mock_installer.restart.assert_called_once()


def test_is_enabled_delegates_to_installer(tmp_path: Path) -> None:
    daemon = ServerDaemon(tmp_path, port=9999, cert="c.pem", key="k.pem")
    mock_installer = MagicMock()
    mock_installer.is_enabled.return_value = True
    with patch("dsync.daemon.base.ServiceInstaller.get", return_value=mock_installer):
        assert daemon.is_enabled() is True


def test_is_running_delegates_to_installer(tmp_path: Path) -> None:
    daemon = ServerDaemon(tmp_path, port=9999, cert="c.pem", key="k.pem")
    mock_installer = MagicMock()
    mock_installer.is_running.return_value = False
    with patch("dsync.daemon.base.ServiceInstaller.get", return_value=mock_installer):
        assert daemon.is_running() is False


def test_installer_built_with_daemon_identity(tmp_path: Path) -> None:
    daemon = ServerDaemon(tmp_path, port=9999, cert="c.pem", key="k.pem")
    with patch("dsync.daemon.base.ServiceInstaller.get") as mock_get:
        daemon._installer()

    mock_get.assert_called_once_with(
        "dsync-server",
        daemon.description,
        daemon.command(),
        working_dir=tmp_path,
        log_path=daemon.log_path,
    )
