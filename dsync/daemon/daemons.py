"""Concrete dsync daemons.

Currently only the sync server runs as a daemon. Further daemons (e.g. an
interval-sync scheduler) are added here as separate :class:`Daemon` subclasses.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from dsync.daemon.base import Daemon

if TYPE_CHECKING:
    from pathlib import Path


def _base_argv(config_dir: Path) -> list[str]:
    """Return the argv prefix that re-invokes dsync for the given config dir.

    Frozen builds run the bundled executable; source checkouts use ``-m dsync``.
    """
    prefix = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "dsync"]
    return [*prefix, "--config-dir", str(config_dir)]


class ServerDaemon(Daemon):
    """Runs ``sync start --mode server`` so peers can connect for backup/mirror."""

    def __init__(self, config_dir: Path, port: int, cert: str, key: str) -> None:
        """Bind the server daemon to a config directory and its server options.

        Args:
            config_dir: Directory holding the dsync configuration.
            port: Network port the server listens on.
            cert: Path to the TLS certificate.
            key: Path to the TLS private key.
        """
        super().__init__(config_dir)
        self.port = port
        self.cert = cert
        self.key = key

    @property
    def service_name(self) -> str:
        """OS-unique service name for the sync server."""
        return "dsync-server"

    @property
    def description(self) -> str:
        """Human-readable description of the sync server daemon."""
        return "dsync - decentralized file sync server daemon"

    def command(self) -> list[str]:
        """Return the argv that starts the sync server."""
        return [
            *_base_argv(self.config_dir),
            "sync",
            "start",
            "--mode",
            "server",
            "--port",
            str(self.port),
            "--cert",
            self.cert,
            "--key",
            self.key,
        ]
