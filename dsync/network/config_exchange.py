"""Pre-sync config exchange between source and peer.

Source and peer exchange their FoldersConfig bidirectionally.
Both sides validate against circular sync conflicts before file transfer.
"""

import asyncio

import yaml

from dsync.config.folder import FoldersConfig, SyncMode
from dsync.network.errors import ConfigConflictError
from dsync.network.p2p_core import (
    async_recv_config,
    async_recv_config_ack,
    async_send_config,
    async_send_config_ack,
)


class ConfigExchange:
    """Abstracts pre-sync config exchange protocol.

    Source (server) sends its FoldersConfig to peer (client) before transfer.
    Peer receives config and acknowledges. No validation yet [1].
    """

    def __init__(self, own_config: FoldersConfig, own_device_id: str):
        """Initialize with own configuration.

        Args:
            own_config: This node's folder configuration.
            own_device_id: This node's device ID (for peer to identify).
        """
        self.own_config = own_config
        self.own_device_id = own_device_id

    async def exchange_and_validate(
        self,
        writer: asyncio.StreamWriter,
        reader: asyncio.StreamReader,
        peer_device_id: str,
        is_source: bool,
    ) -> FoldersConfig:
        """Bidirectional config exchange with conflict validation.

        Args:
            writer: Stream writer to peer.
            reader: Stream reader from peer.
            peer_device_id: Verified device ID of the peer.
            is_source: True if source initiates the sync, False if source receives.

        Returns:
            Peer's FoldersConfig.

        Raises:
            ConfigConflictError: If both sides try to sync the same path as
            BACKUP_TO_PEER, or both use MIRROR mode.
        """
        if is_source:
            # Source sends first, then receives peer config
            await self._send_own_config(writer)
            peer_config = await self._recv_peer_config(reader)
            self._validate_no_circular_conflict(peer_device_id, peer_config)
            await async_send_config_ack(writer)
            print("[+] Config exchange completed (no conflicts)")
            return peer_config
        # Peer receives first, then sends own config
        source_config = await self._recv_peer_config(reader)
        await self._send_own_config(writer)
        self._validate_no_circular_conflict(peer_device_id, source_config)
        await async_recv_config_ack(reader)
        print("[+] Config exchange completed (no conflicts)")
        return source_config

    async def _send_own_config(self, writer: asyncio.StreamWriter) -> None:
        """Serialize and send own FoldersConfig."""
        config_yaml = yaml.safe_dump(self.own_config.model_dump(mode="json")).encode("utf-8")
        print(f"[*] Sending config to peer ({len(config_yaml)} bytes)...")
        await async_send_config(writer, config_yaml)

    async def _recv_peer_config(self, reader: asyncio.StreamReader) -> FoldersConfig:
        """Receive and deserialize peer's FoldersConfig."""
        print("[*] Receiving config from peer...")
        config_yaml = await async_recv_config(reader)
        print(f"[+] Received config ({len(config_yaml)} bytes)")
        config_dict = yaml.safe_load(config_yaml.decode("utf-8"))
        return FoldersConfig.model_validate(config_dict or {})

    def _validate_no_circular_conflict(
        self, peer_device_id: str, peer_config: FoldersConfig
    ) -> None:
        """Validate that no circular sync relationships exist.

        Conflicts:
        1. Both sides have same path as BACKUP_TO_PEER targeting each other
        2. Both sides have same path as MIRROR

        Valid:
        - A: BACKUP_TO_PEER -> B, B: BACKUP_FROM_PEER <- A (correct backup setup)

        Args:
            peer_device_id: The device ID of the peer the source is communicating with.
            peer_config: The peer's folder configuration.

        Raises:
            ConfigConflictError: If a circular sync relationship is detected.
        """
        # Get paths source syncs TO this peer
        source_backup_to_peer = self._get_backup_to_peer_paths(self.own_config, peer_device_id)

        # Get paths peer syncs TO source
        peer_backup_to_source = self._get_backup_to_peer_paths(peer_config, self.own_device_id)

        # Peer's MIRROR paths that include this peer
        source_mirror_paths = self._get_mirror_paths(self.own_config, peer_device_id)

        # Peer's MIRROR paths that include source
        peer_mirror_paths = self._get_mirror_paths(peer_config, self.own_device_id)

        # Find conflicts (same path synced bidirectionally)
        backup_conflicts = source_backup_to_peer & peer_backup_to_source

        if backup_conflicts:
            raise ConfigConflictError(
                f"Bidirectional backup conflict: Both sides configured to backup "
                f"{backup_conflicts} to each other. One side must use "
                f"'backup-from-peer' instead of 'backup-to-peer'."
            )

        # Check for MIRROR conflicts
        mirror_conflicts = source_mirror_paths & peer_mirror_paths
        if mirror_conflicts:
            raise ConfigConflictError(
                f"Mirror conflict: Both sides configured {mirror_conflicts} as MIRROR. "
                f"This can lead to sync loops and data inconsistency."
            )

    @staticmethod
    def _get_backup_to_peer_paths(config: FoldersConfig, target_peer_id: str) -> set[str]:
        """Extract source paths that are configured to sync to target_peer_id.

        Args:
            config: FoldersConfig to analyze.
            target_peer_id: Device ID of the target peer.

        Returns:
            Set of absolute path strings that are synced to target_peer_id.
        """
        paths = set()

        for entry in config.entries:
            if entry.mode == SyncMode.BACKUP_TO_PEER and (
                entry.devices is None or target_peer_id in entry.devices
            ):
                paths.add(str(entry.path))

        return paths

    @staticmethod
    def _get_mirror_paths(config: FoldersConfig, peer_id: str) -> set[str]:
        """Extract source paths that are configured as MIRROR that include peer_id.

        Args:
            config: FoldersConfig to analyze.
            peer_id: Device ID of the peer.

        Returns:
            Set of absolute path strings in MIRROR mode with peer_id.
        """
        paths = set()

        for entry in config.entries:
            if entry.mode == SyncMode.MIRROR and (
                entry.devices is None or peer_id in entry.devices
            ):
                paths.add(str(entry.path))

        return paths


class ConfigExchangeLegacy(ConfigExchange):
    """Backwards-compatible wrapper for old unidirectional exchange."""

    def __init__(self) -> None:
        """Initialize legacy exchange without config (methods accept config as args)."""

    async def exchange_as_source(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        folders_config: FoldersConfig,
    ) -> None:
        """Legacy: Send config from source to peer (no validation)."""
        config_yaml = yaml.safe_dump(folders_config.model_dump(mode="json")).encode("utf-8")
        print(f"[*] Sending config to peer ({len(config_yaml)} bytes)...")
        await async_send_config(writer, config_yaml)
        await async_recv_config_ack(reader)
        print("[+] Config acknowledged by peer")

    async def exchange_as_peer(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> FoldersConfig:
        """Legacy: Receive config from source on peer side (no validation)."""
        print("[*] Receiving config from source...")
        config_yaml = await async_recv_config(reader)
        print(f"[*] Received config ({len(config_yaml)} bytes)")
        config_dict = yaml.safe_load(config_yaml.decode("utf-8"))
        received_config = FoldersConfig.model_validate(config_dict or {})
        await async_send_config_ack(writer)
        print("[*] Config acknowledged")
        return received_config
