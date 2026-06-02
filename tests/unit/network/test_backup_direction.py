import asyncio
from pathlib import Path

import pytest

from dsync.network.backup_direction import BackupSession, DirectionViolationError, TransferRole


def test_as_source_sets_source_role() -> None:
    session = BackupSession.as_source()
    assert session.role is TransferRole.SOURCE


def test_as_peer_sets_peer_role() -> None:
    session = BackupSession.as_peer()
    assert session.role is TransferRole.PEER


async def test_source_can_send_empty_files() -> None:
    session = BackupSession.as_source()
    await session.send_files(None, (), Path("/"))  # type: ignore[arg-type]


async def test_source_cannot_receive() -> None:
    session = BackupSession.as_source()
    with pytest.raises(DirectionViolationError):
        await session.receive_files(None, Path("/"))  # type: ignore[arg-type]


async def test_peer_can_receive_from_empty_stream() -> None:
    session = BackupSession.as_peer()
    reader = asyncio.StreamReader()
    reader.feed_eof()
    await session.receive_files(reader, Path("/tmp"))


async def test_peer_cannot_send() -> None:
    session = BackupSession.as_peer()
    with pytest.raises(DirectionViolationError):
        await session.send_files(None, (), Path("/"))  # type: ignore[arg-type]


def test_direction_violation_error_is_runtime_error() -> None:
    assert issubclass(DirectionViolationError, RuntimeError)
