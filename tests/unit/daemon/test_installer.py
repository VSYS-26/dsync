from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from dsync.daemon.installer import (
    LaunchdServiceInstaller,
    ServiceInstaller,
    SystemdServiceInstaller,
    WindowsServiceInstaller,
)

_COMMAND = ["dsync", "sync", "start"]


def _windows(tmp_path: Path) -> WindowsServiceInstaller:
    return WindowsServiceInstaller(
        "dsync-server",
        "dsync server",
        _COMMAND,
        working_dir=tmp_path,
        log_path=tmp_path / "logs" / "dsync-server.log",
    )


def _fake_win32service() -> MagicMock:
    fake = MagicMock()
    fake.SC_MANAGER_ALL_ACCESS = "SC_MANAGER_ALL_ACCESS"
    fake.SC_MANAGER_CONNECT = "SC_MANAGER_CONNECT"
    fake.SERVICE_ALL_ACCESS = "SERVICE_ALL_ACCESS"
    fake.SERVICE_QUERY_STATUS = "SERVICE_QUERY_STATUS"
    fake.SERVICE_WIN32_OWN_PROCESS = "SERVICE_WIN32_OWN_PROCESS"
    fake.SERVICE_AUTO_START = "SERVICE_AUTO_START"
    fake.SERVICE_ERROR_NORMAL = "SERVICE_ERROR_NORMAL"
    fake.SERVICE_CONTROL_STOP = "SERVICE_CONTROL_STOP"
    fake.SERVICE_RUNNING = "SERVICE_RUNNING"
    return fake


def _systemd(tmp_path: Path) -> SystemdServiceInstaller:
    return SystemdServiceInstaller(
        "dsync-server",
        "dsync server",
        _COMMAND,
        working_dir=tmp_path,
        log_path=tmp_path / "logs" / "dsync-server.log",
    )


def _launchd(tmp_path: Path) -> LaunchdServiceInstaller:
    return LaunchdServiceInstaller(
        "dsync-server",
        "dsync server",
        _COMMAND,
        working_dir=tmp_path,
        log_path=tmp_path / "logs" / "dsync-server.log",
    )


# ── ServiceInstaller.get dispatch ────────────────────────────────────────────


def test_get_returns_systemd_on_linux(tmp_path: Path) -> None:
    with patch("dsync.daemon.installer.platform.system", return_value="Linux"):
        installer = ServiceInstaller.get(
            "id", "desc", _COMMAND, working_dir=tmp_path, log_path=tmp_path / "log"
        )
    assert isinstance(installer, SystemdServiceInstaller)


def test_get_returns_launchd_on_darwin(tmp_path: Path) -> None:
    with patch("dsync.daemon.installer.platform.system", return_value="Darwin"):
        installer = ServiceInstaller.get(
            "id", "desc", _COMMAND, working_dir=tmp_path, log_path=tmp_path / "log"
        )
    assert isinstance(installer, LaunchdServiceInstaller)


def test_get_returns_windows_on_windows(tmp_path: Path) -> None:
    with patch("dsync.daemon.installer.platform.system", return_value="Windows"):
        installer = ServiceInstaller.get(
            "id", "desc", _COMMAND, working_dir=tmp_path, log_path=tmp_path / "log"
        )
    assert isinstance(installer, WindowsServiceInstaller)


def test_get_raises_on_unsupported_platform(tmp_path: Path) -> None:
    with (
        patch("dsync.daemon.installer.platform.system", return_value="Plan9"),
        pytest.raises(NotImplementedError),
    ):
        ServiceInstaller.get(
            "id", "desc", _COMMAND, working_dir=tmp_path, log_path=tmp_path / "log"
        )


# ── SystemdServiceInstaller ───────────────────────────────────────────────────


def test_systemd_generate_service_file_includes_identity(tmp_path: Path) -> None:
    installer = _systemd(tmp_path)
    content = installer._generate_service_file()
    assert "dsync server" in content
    assert "dsync sync start" in content
    assert str(tmp_path) in content


def test_systemd_run_systemctl_uses_sudo(tmp_path: Path) -> None:
    installer = _systemd(tmp_path)
    with patch("dsync.daemon.installer.subprocess.run") as mock_run:
        installer._run_systemctl("daemon-reload")
    mock_run.assert_called_once_with(
        ["sudo", "systemctl", "daemon-reload"], check=True, capture_output=True
    )


def test_systemd_enable_writes_unit_and_runs_systemctl(tmp_path: Path) -> None:
    installer = _systemd(tmp_path)
    installer.service_file = tmp_path / "dsync-server.service"
    with patch("dsync.daemon.installer.subprocess.run") as mock_run:
        installer.enable()

    assert installer.service_file.is_file()
    calls = [c.args[0] for c in mock_run.call_args_list]
    assert ["sudo", "systemctl", "daemon-reload"] in calls
    assert ["sudo", "systemctl", "enable", "dsync-server.service"] in calls
    assert ["sudo", "systemctl", "start", "dsync-server.service"] in calls


def test_systemd_enable_falls_back_to_sudo_tee_on_permission_error(tmp_path: Path) -> None:
    installer = _systemd(tmp_path)
    with (
        patch.object(Path, "write_text", side_effect=PermissionError),
        patch("dsync.daemon.installer.subprocess.run") as mock_run,
    ):
        installer.enable()

    calls = [c.args[0] for c in mock_run.call_args_list]
    assert ["sudo", "tee", str(installer.service_file)] in calls


def test_systemd_disable_removes_unit_and_runs_systemctl(tmp_path: Path) -> None:
    installer = _systemd(tmp_path)
    installer.service_file = tmp_path / "dsync-server.service"
    installer.service_file.write_text("unit")
    with patch("dsync.daemon.installer.subprocess.run") as mock_run:
        installer.disable()

    assert not installer.service_file.exists()
    calls = [c.args[0] for c in mock_run.call_args_list]
    assert ["sudo", "systemctl", "stop", "dsync-server.service"] in calls
    assert ["sudo", "systemctl", "disable", "dsync-server.service"] in calls
    assert ["sudo", "systemctl", "daemon-reload"] in calls


def test_systemd_disable_swallows_stop_failure(tmp_path: Path) -> None:
    installer = _systemd(tmp_path)

    def _run(args, **kwargs):
        if "stop" in args:
            raise subprocess.CalledProcessError(1, args)
        return MagicMock()

    with patch("dsync.daemon.installer.subprocess.run", side_effect=_run):
        installer.disable()


def test_systemd_disable_falls_back_to_sudo_rm_on_permission_error(tmp_path: Path) -> None:
    installer = _systemd(tmp_path)
    installer.service_file = tmp_path / "dsync-server.service"
    installer.service_file.write_text("unit")
    with (
        patch.object(Path, "unlink", side_effect=PermissionError),
        patch("dsync.daemon.installer.subprocess.run") as mock_run,
    ):
        installer.disable()

    calls = [c.args[0] for c in mock_run.call_args_list]
    assert ["sudo", "rm", str(installer.service_file)] in calls


def test_systemd_restart_calls_systemctl_restart(tmp_path: Path) -> None:
    installer = _systemd(tmp_path)
    with patch("dsync.daemon.installer.subprocess.run") as mock_run:
        installer.restart()
    mock_run.assert_called_once_with(
        ["sudo", "systemctl", "restart", "dsync-server.service"], check=True, capture_output=True
    )


@pytest.mark.parametrize(("returncode", "expected"), [(0, True), (1, False)])
def test_systemd_is_enabled(tmp_path: Path, returncode: int, expected: bool) -> None:
    installer = _systemd(tmp_path)
    mock_result = MagicMock(returncode=returncode)
    with patch("dsync.daemon.installer.subprocess.run", return_value=mock_result):
        assert installer.is_enabled() is expected


def test_systemd_is_enabled_false_on_exception(tmp_path: Path) -> None:
    installer = _systemd(tmp_path)
    with patch("dsync.daemon.installer.subprocess.run", side_effect=FileNotFoundError):
        assert installer.is_enabled() is False


@pytest.mark.parametrize(("returncode", "expected"), [(0, True), (3, False)])
def test_systemd_is_running(tmp_path: Path, returncode: int, expected: bool) -> None:
    installer = _systemd(tmp_path)
    mock_result = MagicMock(returncode=returncode)
    with patch("dsync.daemon.installer.subprocess.run", return_value=mock_result):
        assert installer.is_running() is expected


def test_systemd_is_running_false_on_exception(tmp_path: Path) -> None:
    installer = _systemd(tmp_path)
    with patch("dsync.daemon.installer.subprocess.run", side_effect=FileNotFoundError):
        assert installer.is_running() is False


# ── LaunchdServiceInstaller ───────────────────────────────────────────────────


def test_launchd_generate_plist_includes_identity(tmp_path: Path) -> None:
    installer = _launchd(tmp_path)
    content = installer._generate_plist()
    assert "dsync-server" in content
    assert "<string>dsync</string>" in content
    assert str(tmp_path) in content


def test_launchd_enable_writes_plist_and_loads(tmp_path: Path) -> None:
    installer = _launchd(tmp_path)
    with patch("dsync.daemon.installer.subprocess.run") as mock_run:
        installer.enable()

    assert installer.plist_file.is_file()
    mock_run.assert_called_once_with(
        ["launchctl", "load", str(installer.plist_file)], check=True, capture_output=True
    )


def test_launchd_disable_unloads_and_removes_plist(tmp_path: Path) -> None:
    installer = _launchd(tmp_path)
    installer.plist_file.parent.mkdir(parents=True, exist_ok=True)
    installer.plist_file.write_text("plist")
    with patch("dsync.daemon.installer.subprocess.run") as mock_run:
        installer.disable()

    assert not installer.plist_file.exists()
    mock_run.assert_called_once_with(
        ["launchctl", "unload", str(installer.plist_file)], check=True, capture_output=True
    )


def test_launchd_disable_noop_when_plist_absent(tmp_path: Path) -> None:
    installer = _launchd(tmp_path)
    with patch("dsync.daemon.installer.subprocess.run") as mock_run:
        installer.disable()
    mock_run.assert_not_called()


def test_launchd_disable_swallows_unload_failure(tmp_path: Path) -> None:
    installer = _launchd(tmp_path)
    installer.plist_file.parent.mkdir(parents=True, exist_ok=True)
    installer.plist_file.write_text("plist")
    with patch(
        "dsync.daemon.installer.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, ["launchctl"]),
    ):
        installer.disable()

    assert not installer.plist_file.exists()


def test_launchd_is_enabled_reflects_plist_existence(tmp_path: Path) -> None:
    installer = _launchd(tmp_path)
    assert installer.is_enabled() is False
    installer.plist_file.parent.mkdir(parents=True, exist_ok=True)
    installer.plist_file.write_text("plist")
    assert installer.is_enabled() is True


@pytest.mark.parametrize(("returncode", "expected"), [(0, True), (1, False)])
def test_launchd_is_running(tmp_path: Path, returncode: int, expected: bool) -> None:
    installer = _launchd(tmp_path)
    mock_result = MagicMock(returncode=returncode)
    with patch("dsync.daemon.installer.subprocess.run", return_value=mock_result):
        assert installer.is_running() is expected


def test_launchd_is_running_false_on_exception(tmp_path: Path) -> None:
    installer = _launchd(tmp_path)
    with patch("dsync.daemon.installer.subprocess.run", side_effect=FileNotFoundError):
        assert installer.is_running() is False


def test_default_restart_disables_then_enables(tmp_path: Path) -> None:
    installer = _launchd(tmp_path)
    with (
        patch.object(installer, "disable") as mock_disable,
        patch.object(installer, "enable") as mock_enable,
    ):
        installer.restart()

    mock_disable.assert_called_once()
    mock_enable.assert_called_once()


# ── WindowsServiceInstaller ───────────────────────────────────────────────────


def test_windows_build_binary_path_quotes_command(tmp_path: Path) -> None:
    installer = _windows(tmp_path)
    assert installer._build_binary_path() == subprocess.list2cmdline(_COMMAND)


def test_windows_enable_creates_and_starts_service(tmp_path: Path) -> None:
    installer = _windows(tmp_path)
    fake = _fake_win32service()
    handle = MagicMock()
    fake.CreateService.return_value = handle

    with patch.dict(sys.modules, {"win32service": fake}):
        installer.enable()

    fake.CreateService.assert_called_once()
    fake.StartService.assert_called_once_with(handle, None)
    assert fake.CloseServiceHandle.call_count == 2


def test_windows_disable_deletes_service(tmp_path: Path) -> None:
    installer = _windows(tmp_path)
    fake = _fake_win32service()
    handle = MagicMock()
    fake.OpenService.return_value = handle

    with patch.dict(sys.modules, {"win32service": fake}):
        installer.disable()

    fake.ControlService.assert_called_once_with(handle, fake.SERVICE_CONTROL_STOP)
    fake.DeleteService.assert_called_once_with(handle)


def test_windows_disable_swallows_missing_service(tmp_path: Path) -> None:
    installer = _windows(tmp_path)
    fake = _fake_win32service()
    fake.OpenService.side_effect = Exception("service does not exist")

    with patch.dict(sys.modules, {"win32service": fake}):
        installer.disable()

    fake.CloseServiceHandle.assert_called_once_with(fake.OpenSCManager.return_value)


def test_windows_is_enabled_true_when_service_opens(tmp_path: Path) -> None:
    installer = _windows(tmp_path)
    fake = _fake_win32service()

    with patch.dict(sys.modules, {"win32service": fake}):
        assert installer.is_enabled() is True


def test_windows_is_enabled_false_on_exception(tmp_path: Path) -> None:
    installer = _windows(tmp_path)
    fake = _fake_win32service()
    fake.OpenService.side_effect = Exception("not installed")

    with patch.dict(sys.modules, {"win32service": fake}):
        assert installer.is_enabled() is False


def test_windows_is_running_true_when_status_running(tmp_path: Path) -> None:
    installer = _windows(tmp_path)
    fake = _fake_win32service()
    fake.QueryServiceStatus.return_value = (0, "SERVICE_RUNNING")

    with patch.dict(sys.modules, {"win32service": fake}):
        assert installer.is_running() is True


def test_windows_is_running_false_when_status_stopped(tmp_path: Path) -> None:
    installer = _windows(tmp_path)
    fake = _fake_win32service()
    fake.QueryServiceStatus.return_value = (0, "SERVICE_STOPPED")

    with patch.dict(sys.modules, {"win32service": fake}):
        assert installer.is_running() is False


def test_windows_is_running_false_on_exception(tmp_path: Path) -> None:
    installer = _windows(tmp_path)
    fake = _fake_win32service()
    fake.OpenService.side_effect = Exception("not installed")

    with patch.dict(sys.modules, {"win32service": fake}):
        assert installer.is_running() is False
