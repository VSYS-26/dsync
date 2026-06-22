import asyncio
from pathlib import Path
import socket

import pytest
import pytest_asyncio
from typer.testing import CliRunner

from dsync.config import DevicesConfig, FoldersConfig


@pytest.fixture
def tmp_config_dir(tmp_path: Path) -> Path:
    (tmp_path / DevicesConfig.FILENAME).write_text("trusted_devices: []\n")
    (tmp_path / FoldersConfig.FILENAME).write_text("entries: []\n")
    return tmp_path


@pytest.fixture
def valid_fp() -> str:
    return "hex-" + "a" * 64


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest_asyncio.fixture
async def stream_pair():
    sock_a, sock_b = socket.socketpair()
    reader_a, writer_a = await asyncio.open_connection(sock=sock_a)
    reader_b, writer_b = await asyncio.open_connection(sock=sock_b)
    yield (reader_a, writer_a), (reader_b, writer_b)
    for w in (writer_a, writer_b):
        if not w.is_closing():
            w.close()
