"""Daemon management across platforms (Linux/systemd, macOS/launchd, Windows/pywin32)."""

from __future__ import annotations

from abc import ABC, abstractmethod
import contextlib
from pathlib import Path
import platform
import subprocess
import sys


class DaemonManager(ABC):
    """Abstract base for daemon managers across platforms."""

    @abstractmethod
    def enable(self, port: int, cert: str, key: str) -> None:
        """Enable daemon mode with given config."""

    @abstractmethod
    def disable(self) -> None:
        """Disable daemon mode."""

    @abstractmethod
    def is_enabled(self) -> bool:
        """Check if daemon is enabled."""

    @abstractmethod
    def is_running(self) -> bool:
        """Check if daemon is currently running."""

    @abstractmethod
    def start(self) -> None:
        """Start the daemon."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the daemon."""

    @classmethod
    def get_manager(cls, config_dir: Path) -> DaemonManager:
        """Factory: get platform-specific daemon manager."""
        system = platform.system()
        if system == "Linux":
            return SystemdDaemonManager(config_dir)
        if system == "Darwin":
            return LaunchdDaemonManager(config_dir)
        if system == "Windows":
            return WindowsServiceManager(config_dir)
        raise NotImplementedError(f"Daemon not supported on {system}")


class SystemdDaemonManager(DaemonManager):
    """Systemd-based daemon manager for Linux."""

    def __init__(self, config_dir: Path) -> None:
        """Initialize with config directory."""
        self.config_dir = config_dir
        self.service_name = "dsync"
        self.service_file = Path(f"/etc/systemd/system/{self.service_name}.service")

    def _run_systemctl(self, *args: str) -> None:
        """Run systemctl command with sudo."""
        cmd = ["sudo", "systemctl", *args]
        subprocess.run(cmd, check=True, capture_output=True)

    def _generate_service_file(self, port: int, cert: str, key: str) -> str:
        """Generate systemd service file content."""
        config_arg = str(self.config_dir)
        return f"""\
[Unit]
Description=dsync - Decentralized file sync daemon
After=network.target
Wants=network-online.target

[Service]
Type=simple
User={Path.home().owner()}
WorkingDirectory={self.config_dir}
ExecStart={sys.executable} -m dsync --config-dir {config_arg} sync start --mode server --port {port} --cert {cert} --key {key}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=dsync

[Install]
WantedBy=multi-user.target
"""

    def enable(self, port: int, cert: str, key: str) -> None:
        """Enable and install daemon service."""
        service_content = self._generate_service_file(port, cert, key)
        try:
            self.service_file.write_text(service_content)
        except PermissionError:
            subprocess.run(
                ["sudo", "tee", str(self.service_file)],
                input=service_content.encode(),
                check=True,
            )
        self._run_systemctl("daemon-reload")
        self._run_systemctl("enable", f"{self.service_name}.service")
        self._run_systemctl("start", f"{self.service_name}.service")

    def disable(self) -> None:
        """Disable and remove daemon service."""
        try:
            self._run_systemctl("stop", f"{self.service_name}.service")
            self._run_systemctl("disable", f"{self.service_name}.service")
        except subprocess.CalledProcessError:
            pass
        if self.service_file.exists():
            try:
                self.service_file.unlink()
            except PermissionError:
                subprocess.run(["sudo", "rm", str(self.service_file)], check=True)
        self._run_systemctl("daemon-reload")

    def is_enabled(self) -> bool:
        """Check if service is enabled."""
        try:
            result = subprocess.run(
                ["systemctl", "is-enabled", f"{self.service_name}.service"],
                capture_output=True,
                text=True,
            )
        except Exception:
            return False
        else:
            return result.returncode == 0

    def is_running(self) -> bool:
        """Check if service is running."""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", f"{self.service_name}.service"],
                capture_output=True,
                text=True,
            )
        except Exception:
            return False
        else:
            return result.returncode == 0

    def start(self) -> None:
        """Start the daemon."""
        self._run_systemctl("start", f"{self.service_name}.service")

    def stop(self) -> None:
        """Stop the daemon."""
        self._run_systemctl("stop", f"{self.service_name}.service")


class LaunchdDaemonManager(DaemonManager):
    """Launchd-based daemon manager for macOS."""

    def __init__(self, config_dir: Path) -> None:
        """Initialize with config directory."""
        self.config_dir = config_dir
        self.label = "com.dsync.daemon"
        self.plist_file = Path.home() / "Library" / "LaunchAgents" / f"{self.label}.plist"

    def _generate_plist(self, port: int, cert: str, key: str) -> str:
        """Generate launchd plist XML content."""
        config_arg = str(self.config_dir)
        log_dir = Path.home() / "Library" / "Logs" / "dsync"
        return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{self.label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>-m</string>
        <string>dsync</string>
        <string>--config-dir</string>
        <string>{config_arg}</string>
        <string>sync</string>
        <string>start</string>
        <string>--mode</string>
        <string>server</string>
        <string>--port</string>
        <string>{port}</string>
        <string>--cert</string>
        <string>{cert}</string>
        <string>--key</string>
        <string>{key}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>{self.config_dir}</string>
    <key>StandardOutPath</key>
    <string>{log_dir}/dsync.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/dsync-error.log</string>
</dict>
</plist>
"""

    def enable(self, port: int, cert: str, key: str) -> None:
        """Install and load launchd agent."""
        self.plist_file.parent.mkdir(parents=True, exist_ok=True)
        log_dir = Path.home() / "Library" / "Logs" / "dsync"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.plist_file.write_text(self._generate_plist(port, cert, key))
        subprocess.run(["launchctl", "load", str(self.plist_file)], check=True, capture_output=True)

    def disable(self) -> None:
        """Unload and remove launchd agent."""
        if self.plist_file.exists():
            with contextlib.suppress(subprocess.CalledProcessError):
                subprocess.run(
                    ["launchctl", "unload", str(self.plist_file)],
                    check=True,
                    capture_output=True,
                )
            self.plist_file.unlink()

    def is_enabled(self) -> bool:
        """Check if plist is installed."""
        return self.plist_file.exists()

    def is_running(self) -> bool:
        """Check if agent is running via launchctl list."""
        try:
            result = subprocess.run(
                ["launchctl", "list", self.label],
                capture_output=True,
                text=True,
            )
        except Exception:
            return False
        else:
            return result.returncode == 0

    def start(self) -> None:
        """Start the agent."""
        subprocess.run(["launchctl", "load", str(self.plist_file)], check=True, capture_output=True)

    def stop(self) -> None:
        """Stop the agent."""
        subprocess.run(
            ["launchctl", "unload", str(self.plist_file)], check=True, capture_output=True
        )


class WindowsServiceManager(DaemonManager):
    """Windows service manager using pywin32."""

    SERVICE_NAME = "dsync"
    SERVICE_DISPLAY = "dsync - Decentralized file sync daemon"

    def __init__(self, config_dir: Path) -> None:
        """Initialize with config directory."""
        self.config_dir = config_dir

    def _get_win32_service(self) -> object:
        """Import win32service, raise clear error if not available."""
        try:
            import win32service  # type: ignore[import-not-found]
        except ImportError as e:
            raise NotImplementedError(
                "pywin32 is required for Windows daemon support. "
                "Install it with: pip install pywin32"
            ) from e
        else:
            return win32service

    def _build_binary_path(self, port: int, cert: str, key: str) -> str:
        """Build the service binary path with arguments."""
        config_arg = str(self.config_dir)
        return (
            f'"{sys.executable}" -m dsync --config-dir "{config_arg}" '
            f"sync start --mode server --port {port} --cert {cert} --key {key}"
        )

    def enable(self, port: int, cert: str, key: str) -> None:
        """Install and start Windows service."""
        import win32service  # type: ignore[import-not-found]

        binary_path = self._build_binary_path(port, cert, key)
        sc_mgr = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
        try:
            handle = win32service.CreateService(
                sc_mgr,
                self.SERVICE_NAME,
                self.SERVICE_DISPLAY,
                win32service.SERVICE_ALL_ACCESS,
                win32service.SERVICE_WIN32_OWN_PROCESS,
                win32service.SERVICE_AUTO_START,
                win32service.SERVICE_ERROR_NORMAL,
                binary_path,
                None,
                0,
                None,
                None,
                None,
            )
            win32service.StartService(handle, None)
            win32service.CloseServiceHandle(handle)
        finally:
            win32service.CloseServiceHandle(sc_mgr)

    def disable(self) -> None:
        """Stop and remove Windows service."""
        import win32service  # type: ignore[import-not-found]

        sc_mgr = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
        try:
            handle = win32service.OpenService(
                sc_mgr, self.SERVICE_NAME, win32service.SERVICE_ALL_ACCESS
            )
            with contextlib.suppress(Exception):
                win32service.ControlService(handle, win32service.SERVICE_CONTROL_STOP)
            win32service.DeleteService(handle)
            win32service.CloseServiceHandle(handle)
        except Exception:
            pass
        finally:
            win32service.CloseServiceHandle(sc_mgr)

    def is_enabled(self) -> bool:
        """Check if service is installed."""
        try:
            import win32service  # type: ignore[import-not-found]

            sc_mgr = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
            try:
                handle = win32service.OpenService(
                    sc_mgr, self.SERVICE_NAME, win32service.SERVICE_QUERY_STATUS
                )
                win32service.CloseServiceHandle(handle)
            finally:
                win32service.CloseServiceHandle(sc_mgr)
        except Exception:
            return False
        else:
            return True

    def is_running(self) -> bool:
        """Check if service is in running state."""
        try:
            import win32service  # type: ignore[import-not-found]

            sc_mgr = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
            try:
                handle = win32service.OpenService(
                    sc_mgr, self.SERVICE_NAME, win32service.SERVICE_QUERY_STATUS
                )
                status = win32service.QueryServiceStatus(handle)
                win32service.CloseServiceHandle(handle)
            finally:
                win32service.CloseServiceHandle(sc_mgr)
        except Exception:
            return False
        else:
            return status[1] == win32service.SERVICE_RUNNING  # type: ignore[possibly-undefined]

    def start(self) -> None:
        """Start the service."""
        import win32service  # type: ignore[import-not-found]

        sc_mgr = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            handle = win32service.OpenService(sc_mgr, self.SERVICE_NAME, win32service.SERVICE_START)
            win32service.StartService(handle, None)
            win32service.CloseServiceHandle(handle)
        finally:
            win32service.CloseServiceHandle(sc_mgr)

    def stop(self) -> None:
        """Stop the service."""
        import win32service  # type: ignore[import-not-found]

        sc_mgr = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            handle = win32service.OpenService(sc_mgr, self.SERVICE_NAME, win32service.SERVICE_STOP)
            win32service.ControlService(handle, win32service.SERVICE_CONTROL_STOP)
            win32service.CloseServiceHandle(handle)
        finally:
            win32service.CloseServiceHandle(sc_mgr)
