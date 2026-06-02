import asyncio
from pathlib import Path

from dsync.config import FolderEntry, FoldersConfig, SyncMode
from dsync.network.config_exchange import ConfigExchange


async def test_exchange_as_source_sends_and_receives_ack(stream_pair) -> None:
    (reader_a, writer_a), (reader_b, writer_b) = stream_pair
    config = FoldersConfig(entries=[FolderEntry(id="f1", path=Path("/data"), mode=SyncMode.MIRROR)])
    exchange = ConfigExchange()

    async def peer_side() -> FoldersConfig:
        return await exchange.exchange_as_peer(reader_b, writer_b)

    source_task = asyncio.create_task(exchange.exchange_as_source(writer_a, reader_a, config))
    peer_task = asyncio.create_task(peer_side())

    await asyncio.gather(source_task, peer_task)


async def test_exchange_as_peer_returns_config(stream_pair) -> None:
    (reader_a, writer_a), (reader_b, writer_b) = stream_pair
    original = FoldersConfig(
        entries=[
            FolderEntry(id="backup", path=Path("/backup"), mode=SyncMode.BACKUP_FROM_PEER),
        ]
    )
    exchange = ConfigExchange()

    async def source_side() -> None:
        await exchange.exchange_as_source(writer_a, reader_a, original)

    async def peer_side() -> FoldersConfig:
        return await exchange.exchange_as_peer(reader_b, writer_b)

    source_task = asyncio.create_task(source_side())
    peer_task = asyncio.create_task(peer_side())
    _, received = await asyncio.gather(source_task, peer_task)

    assert len(received.entries) == 1
    assert received.entries[0].id == "backup"
    assert received.entries[0].mode == SyncMode.BACKUP_FROM_PEER


async def test_exchange_preserves_multiple_entries(stream_pair) -> None:
    (reader_a, writer_a), (reader_b, writer_b) = stream_pair
    original = FoldersConfig(
        entries=[
            FolderEntry(id="f1", path=Path("/a"), mode=SyncMode.MIRROR),
            FolderEntry(id="f2", path=Path("/b"), mode=SyncMode.BACKUP_TO_PEER, devices=["dev-x"]),
        ]
    )
    exchange = ConfigExchange()

    peer_task = asyncio.create_task(exchange.exchange_as_peer(reader_b, writer_b))
    source_task = asyncio.create_task(exchange.exchange_as_source(writer_a, reader_a, original))
    _, received = await asyncio.gather(source_task, peer_task)

    assert len(received.entries) == 2
    assert received.entries[1].devices == ["dev-x"]


async def test_empty_config_exchange(stream_pair) -> None:
    (reader_a, writer_a), (reader_b, writer_b) = stream_pair
    empty = FoldersConfig(entries=[])
    exchange = ConfigExchange()

    peer_task = asyncio.create_task(exchange.exchange_as_peer(reader_b, writer_b))
    source_task = asyncio.create_task(exchange.exchange_as_source(writer_a, reader_a, empty))
    _, received = await asyncio.gather(source_task, peer_task)

    assert received.entries == []
