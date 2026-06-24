"""Pre-sync folder-config exchange over an authenticated QUIC stream.

After AUTH, source and peer exchange their FolderEntry for the folder being
synced. Both sides validate for circular-sync conflicts. If the peer rejects
the source's config, it sends an ERROR frame before closing so the source
surfaces the real reason instead of a network error.

Protocol (in order on the stream):
  1. SOURCE  → CONFIG  (own FolderEntry)
  2. PEER    → CONFIG  (own FolderEntry)  — or ERROR if validation fails
  3. SOURCE  → CONFIG_ACK
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import struct

import yaml

from dsync.config.folder import FolderEntry, SyncMode
from dsync.network.errors import ConfigConflictError, PeerAuthError
from dsync.network.quic_core import (
    MAX_CONFIG_SIZE,
    MsgType,
    async_recv_config_ack,
    async_send_config,
    async_send_config_ack,
    async_send_msg,
)

logger = logging.getLogger(__name__)


class ConfigExchange:
    """Bidirectional folder-config exchange with conflict detection.

    Both sides send their own ``FolderEntry`` and receive the peer's.
    The PEER validates before sending its entry; on failure it sends an
    ERROR frame so the SOURCE can surface a useful error message.
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
        """Source side: send own entry, receive peer's (or ERROR), validate, send ACK.

        Returns:
            The peer's ``FolderEntry`` for this folder.

        Raises:
            ConfigConflictError: Circular sync detected.
            PeerAuthError: Peer rejected our config (message from peer included).
        """
        await self._send_entry(writer)
        peer_entry = await self._recv_entry_or_error(reader)
        self._validate(peer_device_id, peer_entry)
        await async_send_config_ack(writer)
        logger.debug("config exchange ok (source, peer=%s)", peer_device_id)
        return peer_entry

    async def exchange_as_peer(
        self,
        writer: asyncio.StreamWriter,
        reader: asyncio.StreamReader,
        source_device_id: str,
        validate_fn: Callable[[FolderEntry, str], None] | None = None,
    ) -> FolderEntry:
        """Peer side: receive source entry, validate, send own entry, receive ACK.

        Args:
            validate_fn: Optional extra validation called with
                ``(source_entry, source_device_id)`` after the circular-
                conflict check. Raise :class:`PeerAuthError` or
                :class:`ConfigConflictError` to reject; an ERROR frame
                is sent to the source before re-raising.

        Returns:
            The source's ``FolderEntry`` for this folder.

        Raises:
            ConfigConflictError / PeerAuthError: Validation failed (ERROR
                frame already sent to source before raising).
        """
        source_entry = await self._recv_entry(reader)
        try:
            self._validate(source_device_id, source_entry)
            if validate_fn is not None:
                validate_fn(source_entry, source_device_id)
        except (ConfigConflictError, PeerAuthError) as exc:
            await self._send_error(writer, str(exc))
            raise
        await self._send_entry(writer)
        await async_recv_config_ack(reader)
        logger.debug("config exchange ok (peer, source=%s)", source_device_id)
        return source_entry

    # ------------------------------------------------------------------ private

    async def _send_entry(self, writer: asyncio.StreamWriter) -> None:
        payload = yaml.safe_dump(self._own_entry.model_dump(mode="json")).encode("utf-8")
        await async_send_config(writer, payload)

    async def _recv_entry(self, reader: asyncio.StreamReader) -> FolderEntry:
        from dsync.network.quic_core import async_recv_config

        payload = await async_recv_config(reader)
        data = yaml.safe_load(payload.decode("utf-8"))
        return FolderEntry.model_validate(data or {})

    async def _recv_entry_or_error(self, reader: asyncio.StreamReader) -> FolderEntry:
        """Read next frame; if it's ERROR raise with peer's message; else parse as CONFIG."""
        try:
            header = await reader.readexactly(5)
        except asyncio.IncompleteReadError as err:
            raise RuntimeError("Connection closed during config exchange") from err

        msg_type, length = struct.unpack("!BI", header)

        if msg_type == MsgType.ERROR:
            try:
                body = await reader.readexactly(length)
            except asyncio.IncompleteReadError as err:
                raise RuntimeError("Connection closed reading error frame") from err
            reason = body.decode("utf-8", errors="replace")
            raise PeerAuthError(f"Peer rejected config: {reason}")

        if msg_type != MsgType.CONFIG:
            raise RuntimeError(
                f"Expected CONFIG (type {MsgType.CONFIG}) or ERROR, got type {msg_type}"
            )
        if length > MAX_CONFIG_SIZE:
            raise RuntimeError(
                f"Config payload too large: {length} B exceeds limit of {MAX_CONFIG_SIZE} B"
            )
        try:
            payload = await reader.readexactly(length)
        except asyncio.IncompleteReadError as err:
            raise RuntimeError("Connection lost during config reception") from err

        data = yaml.safe_load(payload.decode("utf-8"))
        return FolderEntry.model_validate(data or {})

    async def _send_error(self, writer: asyncio.StreamWriter, reason: str) -> None:
        await async_send_msg(writer, MsgType.ERROR, reason.encode("utf-8"))

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
