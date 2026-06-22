"""Abstract daemon concept: identity plus command, backed by a shared installer."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from dsync.daemon.installer import ServiceInstaller

if TYPE_CHECKING:
    from pathlib import Path


class Daemon(ABC):
    """A single named background service that dsync can install side by side.

    Subclasses supply only the identity and the command to run; the base class
    wires them into the shared installer and exposes enable/disable/status.
    """

    def __init__(self, config_dir: Path) -> None:
        """Bind the daemon to a configuration directory.

        Args:
            config_dir: Directory holding the dsync configuration.
        """
        self.config_dir = config_dir

    @property
    @abstractmethod
    def service_name(self) -> str:
        """OS-unique service name (e.g. ``dsync-server``)."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable service description."""

    @abstractmethod
    def command(self) -> list[str]:
        """Return the full argv the service runs (the ExecStart)."""

    @property
    def log_path(self) -> Path:
        """Per-daemon log file path under ``config_dir/logs``."""
        return self.config_dir / "logs" / f"{self.service_name}.log"

    def _installer(self) -> ServiceInstaller:
        """Build the shared platform installer for this daemon."""
        return ServiceInstaller.get(
            self.service_name,
            self.description,
            self.command(),
            working_dir=self.config_dir,
            log_path=self.log_path,
        )

    def enable(self) -> None:
        """Install and start this daemon."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._installer().enable()

    def disable(self) -> None:
        """Stop and remove this daemon."""
        self._installer().disable()

    def restart(self) -> None:
        """Restart this daemon so it picks up configuration changes."""
        self._installer().restart()

    def is_enabled(self) -> bool:
        """Return whether this daemon is installed."""
        return self._installer().is_enabled()

    def is_running(self) -> bool:
        """Return whether this daemon is currently running."""
        return self._installer().is_running()
