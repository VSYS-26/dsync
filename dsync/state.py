"""Runtime state shared between CLI commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dsync.config import DaemonConfig, DevicesConfig, FoldersConfig, RelaysConfig

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class AppState:
    """Application state for the running dsync process.

    Access from a CLI command via ``typer.Context.obj``::

        def my_command(ctx: typer.Context) -> None:
            state: AppState = ctx.obj
    """

    config_dir: Path
    folders: FoldersConfig
    devices: DevicesConfig
    relays: RelaysConfig = field(default_factory=RelaysConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)

    @classmethod
    def load(cls, config_dir: Path) -> AppState:
        """Load all configs from ``config_dir`` and resolve folder device lists.

        Folders with no explicit device list inherit all trusted device ids.
        This is the shared resolution used by both the CLI callback and the
        scheduler runner.

        Args:
            config_dir: Directory holding the dsync YAML configuration.

        Returns:
            A fully resolved ``AppState``.

        Raises:
            ValueError: If a folder references a device id absent from
                devices.yaml.
        """
        folders = FoldersConfig.load(config_dir)
        devices = DevicesConfig.load(config_dir)
        relays = RelaysConfig.load(config_dir)
        daemon = DaemonConfig.load(config_dir)

        relay_ids = {r.id for r in relays.relays}
        for device in devices.trusted_devices:
            if device.relay_id not in relay_ids:
                raise ValueError(
                    f"Device {device.id} references relay_id '{device.relay_id}' "
                    "which is not listed in relays.yaml"
                )

        trusted_device_ids = [dev.id for dev in devices.trusted_devices]
        resolved_entries = []
        for entry in folders.entries:
            if entry.devices is None:
                resolved_entries.append(entry.model_copy(update={"devices": trusted_device_ids}))
            else:
                for device_id in entry.devices:
                    if device_id not in trusted_device_ids:
                        raise ValueError(
                            f"Trusted device {device_id} of folder {entry.id} "
                            "is not listed in devices.yaml"
                        )
                resolved_entries.append(entry)
        folders.entries = resolved_entries

        return cls(
            config_dir=config_dir, folders=folders, devices=devices, relays=relays, daemon=daemon
        )
