"""Shared OS service installers (Linux/systemd, macOS/launchd, Windows/pywin32).

One installer per OS, not per daemon: the caller passes in the service identity,
command and paths, so every daemon reuses the same code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import contextlib
from pathlib import Path
import platform
import subprocess


class ServiceInstaller(ABC):
    """Install, remove and query a single named OS service.

    One subclass per operating system. The service identity and command are
    passed in, so multiple services can coexist.
    """

    def __init__(
        self,
        service_name: str,
        description: str,
        command: list[str],
        working_dir: Path,
        log_path: Path,
    ) -> None:
        """Store service identity, command argv and paths.

        Args:
            service_name: OS-unique service name (e.g. ``dsync-server``).
            description: Human-readable service description.
            command: Full argv the service runs (the ExecStart).
            working_dir: Working directory for the service process.
            log_path: File the service should log to (used by launchd/Windows).
        """
        self.service_name = service_name
        self.description = description
        self.command = command
        self.working_dir = working_dir
        self.log_path = log_path

    @abstractmethod
    def enable(self) -> None:
        """Install, enable on boot and start the service."""

    @abstractmethod
    def disable(self) -> None:
        """Stop, disable and remove the service."""

    @abstractmethod
    def is_enabled(self) -> bool:
        """Return whether the service is installed/enabled."""

    @abstractmethod
    def is_running(self) -> bool:
        """Return whether the service is currently running."""

    def restart(self) -> None:
        """Restart the service so it picks up configuration changes.

        Default: remove and reinstall. Subclasses may override with a lighter
        native restart that keeps the installed unit in place.
        """
        self.disable()
        self.enable()

    @classmethod
    def get(
        cls,
        service_name: str,
        description: str,
        command: list[str],
        working_dir: Path,
        log_path: Path,
    ) -> ServiceInstaller:
        """Return the platform-specific installer for the current OS.

        Args:
            service_name: OS-unique service name.
            description: Human-readable service description.
            command: Full argv the service runs.
            working_dir: Working directory for the service process.
            log_path: File the service should log to.

        Returns:
            A concrete :class:`ServiceInstaller` for the current platform.

        Raises:
            NotImplementedError: If the platform is unsupported.
        """
        system = platform.system()
        if system == "Linux":
            return SystemdServiceInstaller(
                service_name, description, command, working_dir, log_path
            )
        if system == "Darwin":
            return LaunchdServiceInstaller(
                service_name, description, command, working_dir, log_path
            )
        if system == "Windows":
            return WindowsServiceInstaller(
                service_name, description, command, working_dir, log_path
            )
        raise NotImplementedError(f"Daemon not supported on {system}")


class SystemdServiceInstaller(ServiceInstaller):
    """systemd-based installer for Linux."""

    def __init__(
        self,
        service_name: str,
        description: str,
        command: list[str],
        working_dir: Path,
        log_path: Path,
    ) -> None:
        """Resolve the unit file path under ``/etc/systemd/system``."""
        super().__init__(service_name, description, command, working_dir, log_path)
        self.service_file = Path(f"/etc/systemd/system/{service_name}.service")

    def _run_systemctl(self, *args: str) -> None:
        """Run a systemctl command with sudo."""
        subprocess.run(["sudo", "systemctl", *args], check=True, capture_output=True)

    def _generate_service_file(self) -> str:
        """Render the systemd unit file."""
        exec_start = " ".join(self.command)
        return f"""\
[Unit]
Description={self.description}
After=network.target
Wants=network-online.target

[Service]
Type=simple
User={Path.home().owner()}
WorkingDirectory={self.working_dir}
ExecStart={exec_start}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier={self.service_name}

[Install]
WantedBy=multi-user.target
"""

    def enable(self) -> None:
        """Write the unit, then enable and start it."""
        service_content = self._generate_service_file()
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
        """Stop, disable and remove the unit."""
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

    def restart(self) -> None:
        """Restart via systemctl, keeping the installed unit file in place."""
        self._run_systemctl("restart", f"{self.service_name}.service")

    def is_enabled(self) -> bool:
        """Check via ``systemctl is-enabled``."""
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
        """Check via ``systemctl is-active``."""
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


class LaunchdServiceInstaller(ServiceInstaller):
    """launchd-based installer for macOS."""

    def __init__(
        self,
        service_name: str,
        description: str,
        command: list[str],
        working_dir: Path,
        log_path: Path,
    ) -> None:
        """Resolve the launchd label and plist path."""
        super().__init__(service_name, description, command, working_dir, log_path)
        self.label = service_name
        self.plist_file = Path.home() / "Library" / "LaunchAgents" / f"{self.label}.plist"

    def _generate_plist(self) -> str:
        """Render the launchd plist."""
        error_log = self.log_path.with_name(f"{self.log_path.stem}-error{self.log_path.suffix}")
        args_xml = "\n".join(f"        <string>{arg}</string>" for arg in self.command)
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
{args_xml}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>{self.working_dir}</string>
    <key>StandardOutPath</key>
    <string>{self.log_path}</string>
    <key>StandardErrorPath</key>
    <string>{error_log}</string>
</dict>
</plist>
"""

    def enable(self) -> None:
        """Write the plist and load the agent."""
        self.plist_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.plist_file.write_text(self._generate_plist())
        subprocess.run(["launchctl", "load", str(self.plist_file)], check=True, capture_output=True)

    def disable(self) -> None:
        """Unload and remove the agent."""
        if self.plist_file.exists():
            with contextlib.suppress(subprocess.CalledProcessError):
                subprocess.run(
                    ["launchctl", "unload", str(self.plist_file)],
                    check=True,
                    capture_output=True,
                )
            self.plist_file.unlink()

    def is_enabled(self) -> bool:
        """Check whether the plist is installed."""
        return self.plist_file.exists()

    def is_running(self) -> bool:
        """Check via ``launchctl list``."""
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


class WindowsServiceInstaller(ServiceInstaller):
    """Windows service installer using pywin32."""

    def _build_binary_path(self) -> str:
        """Quote the command into the service binary path."""
        return subprocess.list2cmdline(self.command)

    def enable(self) -> None:
        """Install and start the Windows service."""
        import win32service

        binary_path = self._build_binary_path()
        sc_mgr = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
        try:
            handle = win32service.CreateService(
                sc_mgr,
                self.service_name,
                self.description,
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
        """Stop and remove the Windows service."""
        import win32service

        sc_mgr = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
        try:
            handle = win32service.OpenService(
                sc_mgr, self.service_name, win32service.SERVICE_ALL_ACCESS
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
        """Check whether the service is installed."""
        try:
            import win32service

            sc_mgr = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
            try:
                handle = win32service.OpenService(
                    sc_mgr, self.service_name, win32service.SERVICE_QUERY_STATUS
                )
                win32service.CloseServiceHandle(handle)
            finally:
                win32service.CloseServiceHandle(sc_mgr)
        except Exception:
            return False
        else:
            return True

    def is_running(self) -> bool:
        """Check whether the service is in the running state."""
        try:
            import win32service

            sc_mgr = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
            try:
                handle = win32service.OpenService(
                    sc_mgr, self.service_name, win32service.SERVICE_QUERY_STATUS
                )
                status = win32service.QueryServiceStatus(handle)
                win32service.CloseServiceHandle(handle)
            finally:
                win32service.CloseServiceHandle(sc_mgr)
        except Exception:
            return False
        else:
            return bool(status[1] == win32service.SERVICE_RUNNING)
