import asyncio
from pathlib import Path

import pytest

from dsync.config import FolderEntry, FoldersConfig, SyncMode
from dsync.network.config_exchange import ConfigExchange, ConfigExchangeLegacy
from dsync.network.errors import ConfigConflictError


async def test_exchange_as_source_sends_and_receives_ack(stream_pair) -> None:
    (reader_a, writer_a), (reader_b, writer_b) = stream_pair
    config = FoldersConfig(entries=[FolderEntry(id="f1", path=Path("/data"), mode=SyncMode.MIRROR)])
    exchange = ConfigExchangeLegacy()

    async def peer_side() -> FoldersConfig:
        return await exchange.exchange_as_peer(reader_b, writer_b)

    source_task = asyncio.create_task(exchange.exchange_as_source(reader_a, writer_a, config))
    peer_task = asyncio.create_task(peer_side())

    await asyncio.gather(source_task, peer_task)


async def test_exchange_as_peer_returns_config(stream_pair) -> None:
    (reader_a, writer_a), (reader_b, writer_b) = stream_pair
    original = FoldersConfig(
        entries=[
            FolderEntry(id="backup", path=Path("/backup"), mode=SyncMode.BACKUP_FROM_PEER),
        ]
    )
    exchange = ConfigExchangeLegacy()

    async def source_side() -> None:
        await exchange.exchange_as_source(reader_a, writer_a, original)

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
    exchange = ConfigExchangeLegacy()

    peer_task = asyncio.create_task(exchange.exchange_as_peer(reader_b, writer_b))
    source_task = asyncio.create_task(exchange.exchange_as_source(reader_a, writer_a, original))
    _, received = await asyncio.gather(source_task, peer_task)

    assert len(received.entries) == 2
    assert received.entries[1].devices == ["dev-x"]


async def test_empty_config_exchange(stream_pair) -> None:
    (reader_a, writer_a), (reader_b, writer_b) = stream_pair
    empty = FoldersConfig(entries=[])
    exchange = ConfigExchangeLegacy()

    peer_task = asyncio.create_task(exchange.exchange_as_peer(reader_b, writer_b))
    source_task = asyncio.create_task(exchange.exchange_as_source(reader_a, writer_a, empty))
    _, received = await asyncio.gather(source_task, peer_task)

    assert received.entries == []


# ── ConfigExchange.exchange_and_validate ──────────────────────────────────────


async def test_exchange_and_validate_valid_backup_setup_no_conflict(stream_pair) -> None:
    (reader_a, writer_a), (reader_b, writer_b) = stream_pair
    config_a = FoldersConfig(
        entries=[
            FolderEntry(
                id="f1", path=Path("/data/a"), mode=SyncMode.BACKUP_TO_PEER, devices=["dev-b"]
            )
        ]
    )
    config_b = FoldersConfig(
        entries=[
            FolderEntry(
                id="f1", path=Path("/data/a"), mode=SyncMode.BACKUP_FROM_PEER, devices=["dev-a"]
            )
        ]
    )
    exchange_a = ConfigExchange(config_a, own_device_id="dev-a")
    exchange_b = ConfigExchange(config_b, own_device_id="dev-b")

    source_task = asyncio.create_task(
        exchange_a.exchange_and_validate(writer_a, reader_a, peer_device_id="dev-b", is_source=True)
    )
    peer_task = asyncio.create_task(
        exchange_b.exchange_and_validate(
            writer_b, reader_b, peer_device_id="dev-a", is_source=False
        )
    )
    source_received, peer_received = await asyncio.gather(source_task, peer_task)

    assert source_received.entries[0].mode == SyncMode.BACKUP_FROM_PEER
    assert peer_received.entries[0].mode == SyncMode.BACKUP_TO_PEER


async def test_exchange_and_validate_bidirectional_backup_conflict_raises(stream_pair) -> None:
    (reader_a, writer_a), (reader_b, writer_b) = stream_pair
    config_a = FoldersConfig(
        entries=[
            FolderEntry(
                id="f1", path=Path("/data/a"), mode=SyncMode.BACKUP_TO_PEER, devices=["dev-b"]
            )
        ]
    )
    config_b = FoldersConfig(
        entries=[
            FolderEntry(
                id="f1", path=Path("/data/a"), mode=SyncMode.BACKUP_TO_PEER, devices=["dev-a"]
            )
        ]
    )
    exchange_a = ConfigExchange(config_a, own_device_id="dev-a")
    exchange_b = ConfigExchange(config_b, own_device_id="dev-b")

    source_task = asyncio.create_task(
        exchange_a.exchange_and_validate(writer_a, reader_a, peer_device_id="dev-b", is_source=True)
    )
    peer_task = asyncio.create_task(
        exchange_b.exchange_and_validate(
            writer_b, reader_b, peer_device_id="dev-a", is_source=False
        )
    )

    with pytest.raises(ConfigConflictError, match="Bidirectional backup conflict"):
        await asyncio.gather(source_task, peer_task)


async def test_exchange_and_validate_mirror_conflict_raises(stream_pair) -> None:
    (reader_a, writer_a), (reader_b, writer_b) = stream_pair
    config_a = FoldersConfig(
        entries=[FolderEntry(id="f1", path=Path("/shared"), mode=SyncMode.MIRROR)]
    )
    config_b = FoldersConfig(
        entries=[FolderEntry(id="f1", path=Path("/shared"), mode=SyncMode.MIRROR)]
    )
    exchange_a = ConfigExchange(config_a, own_device_id="dev-a")
    exchange_b = ConfigExchange(config_b, own_device_id="dev-b")

    source_task = asyncio.create_task(
        exchange_a.exchange_and_validate(writer_a, reader_a, peer_device_id="dev-b", is_source=True)
    )
    peer_task = asyncio.create_task(
        exchange_b.exchange_and_validate(
            writer_b, reader_b, peer_device_id="dev-a", is_source=False
        )
    )

    with pytest.raises(ConfigConflictError, match="Mirror conflict"):
        await asyncio.gather(source_task, peer_task)
