"""Tests for ``dsync.network.local_ipc``: roundtripping requests/responses
over the Unix-domain-socket between the daemon and ``run_backup``.

We don't spin up a real ``RelayDaemon`` here — the IPC layer is tested in
isolation with a stub handler. The daemon-side integration test in
``tests/integration/`` exercises the full path.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import pytest

from dsync.network.local_ipc import (
    IpcResponse,
    LocalControlClient,
    LocalControlServer,
    SyncFolderRequest,
)

if TYPE_CHECKING:
    from pathlib import Path


async def _start_server(
    socket_path: Path,
    handler: object,
) -> LocalControlServer:
    server = LocalControlServer(socket_path=socket_path, handler=handler)  # type: ignore[arg-type]
    await server.start()
    return server


async def test_request_response_roundtrip(tmp_path: Path) -> None:
    """Client sends a SyncFolderRequest; server returns a parsed IpcResponse."""
    seen: list[dict[str, object]] = []

    async def handler(payload: dict[str, object]) -> dict[str, object]:
        seen.append(payload)
        return {"status": "ok", "reason": None}

    server = await _start_server(tmp_path / "daemon.sock", handler)
    try:
        client = LocalControlClient(socket_path=server.socket_path)
        response = await client.request(
            SyncFolderRequest(folder_id="notes", peer_id="laptop-anna"),
        )
        assert response == IpcResponse(status="ok", reason=None)
        assert seen == [
            {"op": "sync_folder", "folder_id": "notes", "peer_id": "laptop-anna"},
        ]
    finally:
        await server.close()


async def test_discover_via_pointer_file(tmp_path: Path) -> None:
    """LocalControlClient.discover finds the daemon via relay.current."""

    async def handler(payload: dict[str, object]) -> dict[str, object]:
        return {"status": "ok", "reason": "discovered"}

    server = await _start_server(tmp_path / "daemon.sock", handler)
    try:
        client = LocalControlClient.discover(ipc_dir=tmp_path)
        response = await client.request(SyncFolderRequest(folder_id="f", peer_id="p"))
        assert response.status == "ok"
        assert response.reason == "discovered"
    finally:
        await server.close()


async def test_discover_no_daemon_raises(tmp_path: Path) -> None:
    """Discover errors clearly when the pointer file is missing."""
    with pytest.raises(FileNotFoundError, match="pointer file missing"):
        LocalControlClient.discover(ipc_dir=tmp_path)


async def test_handler_exception_returns_error_response(tmp_path: Path) -> None:
    """If the daemon's handler raises, the client gets an error response."""

    async def handler(payload: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("relay unreachable")

    server = await _start_server(tmp_path / "daemon.sock", handler)
    try:
        client = LocalControlClient(socket_path=server.socket_path)
        response = await client.request(SyncFolderRequest(folder_id="x", peer_id="y"))
        assert response.status == "error"
        assert response.reason is not None
        assert "relay unreachable" in response.reason
    finally:
        await server.close()


async def test_oversized_frame_rejected(tmp_path: Path) -> None:
    """An oversized inbound frame is rejected before the body is read."""
    from dsync.network.local_ipc import MAX_IPC_BODY_SIZE

    async def handler(payload: dict[str, object]) -> dict[str, object]:
        return {"status": "ok", "reason": None}

    server = await _start_server(tmp_path / "daemon.sock", handler)
    try:
        reader, writer = await asyncio.open_unix_connection(path=str(server.socket_path))
        # Claim a huge frame.
        writer.write((MAX_IPC_BODY_SIZE + 1).to_bytes(4, "big"))
        await writer.drain()
        # Server should respond with an error frame and close.
        header = await reader.readexactly(4)
        length = int.from_bytes(header, "big")
        body = await reader.readexactly(length)
        import json

        payload = json.loads(body.decode("utf-8"))
        assert payload["status"] == "error"
        assert "too large" in (payload["reason"] or "")
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    finally:
        await server.close()


async def test_pointer_file_cleaned_up_on_close(tmp_path: Path) -> None:
    """After server.close(), the relay.current pointer is removed."""

    async def handler(payload: dict[str, object]) -> dict[str, object]:
        return {"status": "ok"}

    server = await _start_server(tmp_path / "daemon.sock", handler)
    pointer = tmp_path / "relay.current"
    assert pointer.is_file()
    await server.close()
    assert not pointer.exists()
