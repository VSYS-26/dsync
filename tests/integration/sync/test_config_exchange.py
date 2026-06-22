import asyncio
from pathlib import Path

import yaml

from dsync.config import FolderEntry, FoldersConfig, SyncMode
from dsync.network.p2p_core import (
    async_recv_config,
    async_recv_config_ack,
    async_send_config,
    async_send_config_ack,
)


async def test_config_roundtrip(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    original = FoldersConfig(
        entries=[FolderEntry(id="f1", path=Path("/data"), mode=SyncMode.MIRROR)]
    )
    config_bytes = yaml.dump(original.model_dump(mode="json")).encode()

    await async_send_config(writer_a, config_bytes)
    received = await async_recv_config(reader_b)

    assert received == config_bytes
    loaded = FoldersConfig.model_validate(yaml.safe_load(received.decode()))
    assert loaded.entries[0].id == "f1"
    assert loaded.entries[0].mode == SyncMode.MIRROR


async def test_config_ack_exchange(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    await async_send_config_ack(writer_a)
    await async_recv_config_ack(reader_b)


async def test_full_config_exchange_sequence(stream_pair) -> None:
    (reader_a, writer_a), (reader_b, writer_b) = stream_pair

    config = FoldersConfig(entries=[])
    config_bytes = yaml.dump(config.model_dump(mode="json")).encode()

    send_task = asyncio.create_task(async_send_config(writer_a, config_bytes))
    recv_task = asyncio.create_task(async_recv_config(reader_b))
    await send_task
    received = await recv_task
    assert received == config_bytes

    ack_send = asyncio.create_task(async_send_config_ack(writer_b))
    ack_recv = asyncio.create_task(async_recv_config_ack(reader_a))
    await asyncio.gather(ack_send, ack_recv)


async def test_config_with_multiple_entries(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    original = FoldersConfig(
        entries=[
            FolderEntry(id="f1", path=Path("/data/a"), mode=SyncMode.MIRROR),
            FolderEntry(
                id="f2",
                path=Path("/data/b"),
                mode=SyncMode.BACKUP_TO_PEER,
                devices=["dev-x"],
            ),
        ]
    )
    config_bytes = yaml.dump(original.model_dump(mode="json")).encode()

    await async_send_config(writer_a, config_bytes)
    received = await async_recv_config(reader_b)
    loaded = FoldersConfig.model_validate(yaml.safe_load(received.decode()))

    assert len(loaded.entries) == 2
    assert loaded.entries[1].devices == ["dev-x"]
