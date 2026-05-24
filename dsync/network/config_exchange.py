"""Pre-sync config exchange between source and peer.

Source decides which folders sync to which peers.
Peer receives config, has no local configuration for this sync.
Config is exchanged BEFORE any file transfer begins.
"""

import asyncio

import yaml

from dsync.config.folder import FoldersConfig
from dsync.network.quic_core import (
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

    async def exchange_as_source(
        self,
        writer: asyncio.StreamWriter,
        reader: asyncio.StreamReader,
        folders_config: FoldersConfig,
    ) -> None:
        """Send config from source to peer.

        Args:
            writer: Stream writer to peer.
            reader: Stream reader from peer.
            folders_config: Source's folder configuration.
        """
        config_yaml = yaml.safe_dump(
            folders_config.model_dump(mode="json")
        ).encode("utf-8")
        print(f"[*] Sending config to peer ({len(config_yaml)} bytes)...")
        await async_send_config(writer, config_yaml)
        await async_recv_config_ack(reader)
        print("[+] Config acknowledged by peer")

    async def exchange_as_peer(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> FoldersConfig:
        """Receive config from source on peer side.

        Args:
            reader: Stream reader from source.
            writer: Stream writer to source.

        Returns:
            Received FoldersConfig from source.
        """
        print("[*] Receiving config from source...")
        config_yaml = await async_recv_config(reader)
        print(f"[+] Received config ({len(config_yaml)} bytes)")
        config_dict = yaml.safe_load(config_yaml.decode("utf-8"))
        received_config = FoldersConfig.model_validate(config_dict or {})
        await async_send_config_ack(writer)
        print("[*] Config acknowledged")
        return received_config
