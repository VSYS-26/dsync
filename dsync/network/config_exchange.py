"""Pre-sync folder-config exchange over an authenticated QUIC stream.

After AUTH, source and peer exchange their FolderEntry for the folder being
synced. Both sides validate for circular-sync conflicts before file transfer
begins. The CONFIG / CONFIG_ACK framing from ``quic_core`` is reused.
"""

from __future__ import annotations

import asyncio
import logging

import yaml

from dsync.config.folder import FolderEntry, SyncMode
from dsync.network.errors import ConfigConflictError
from dsync.network.quic_core import (
    async_recv_config,
    async_recv_config_ack,
    async_send_config,
    async_send_config_ack,
)

logger = logging.getLogger(__name__)


class ConfigExchange:
    """Bidirectional folder-config exchange with circular-conflict detection.

    Both sides send their own ``FolderEntry`` for the folder being synced and
    receive the peer's entry. The source sends first; after both entries have
    been exchanged each side validates for circular-sync conflicts.
    """

    def __init__(self, own_entry: FolderEntry, own_device_id: str) -> None:
        self._own_entry = own_entry
        self._own_device_id = own_device_id

    async def exchange_as_source(
        self,
        writer: asyncio.StreamWriter,
        reader: asyncio.StreamReader,
        peer_device_id: str,
    ) -> FolderEntry:
        """Source side: send own entry, receive peer's, validate, send ACK.

        Returns:
            The peer's ``FolderEntry`` for this folder.

        Raises:
            ConfigConflictError: Circular sync detected.
        """
        await self._send_entry(writer)
        peer_entry = await self._recv_entry(reader)
        self._validate(peer_device_id, peer_entry)
        await async_send_config_ack(writer)
        logger.debug("config exchange ok (source side, peer=%s)", peer_device_id)
        return peer_entry

    async def exchange_as_peer(
        self,
        writer: asyncio.StreamWriter,
        reader: asyncio.StreamReader,
        source_device_id: str,
    ) -> FolderEntry:
        """Peer side: receive source's entry, send own, receive ACK.

        Returns:
            The source's ``FolderEntry`` for this folder.

        Raises:
            ConfigConflictError: Circular sync detected.
        """
        source_entry = await self._recv_entry(reader)
        self._validate(source_device_id, source_entry)
        await self._send_entry(writer)
        await async_recv_config_ack(reader)
        logger.debug("config exchange ok (peer side, source=%s)", source_device_id)
        return source_entry

    async def _send_entry(self, writer: asyncio.StreamWriter) -> None:
        payload = yaml.safe_dump(self._own_entry.model_dump(mode="json")).encode("utf-8")
        await async_send_config(writer, payload)

    async def _recv_entry(self, reader: asyncio.StreamReader) -> FolderEntry:
        payload = await async_recv_config(reader)
        data = yaml.safe_load(payload.decode("utf-8"))
        return FolderEntry.model_validate(data or {})

    def _validate(self, peer_device_id: str, peer_entry: FolderEntry) -> None:
        """Detect circular-sync conflicts between own and peer entry."""
        own_sends_to_peer = self._own_entry.mode == SyncMode.BACKUP_TO_PEER and (
            self._own_entry.devices is None or peer_device_id in self._own_entry.devices
        )
        peer_sends_to_own = peer_entry.mode == SyncMode.BACKUP_TO_PEER and (
            peer_entry.devices is None or self._own_device_id in peer_entry.devices
        )
        if own_sends_to_peer and peer_sends_to_own:
            raise ConfigConflictError(
                f"Bidirectional backup conflict on '{self._own_entry.id}': both sides "
                "configured as backup-to-peer targeting each other. One side must use "
                "backup-from-peer."
            )

        own_mirror = self._own_entry.mode == SyncMode.MIRROR and (
            self._own_entry.devices is None or peer_device_id in self._own_entry.devices
        )
        peer_mirror = peer_entry.mode == SyncMode.MIRROR and (
            peer_entry.devices is None or self._own_device_id in peer_entry.devices
        )
        if own_mirror and peer_mirror and self._own_entry.id == peer_entry.id:
            raise ConfigConflictError(
                f"Mirror conflict on '{self._own_entry.id}': both sides configured as "
                "mirror. This can cause sync loops."
            )
