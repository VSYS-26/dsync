"""Local IPC between the long-running ``dsync relay connect`` daemon and
the one-shot ``dsync sync run_backup`` invocation.

When the user triggers a sync, ``run_backup`` cannot open its own QUIC
connection to a peer — the relay-observed NAT mapping lives on the daemon's
socket. So ``run_backup`` instead asks the daemon (over a local IPC socket)
to perform the sync, and the daemon drives the QUIC + relay protocol.

Wire format (each direction):

    [4-byte big-endian length][UTF-8 JSON body]

Body schemas are defined in this module as Pydantic models so request and
response shapes are self-documenting and validated at the boundary.

POSIX uses a Unix-domain socket placed under ``$XDG_RUNTIME_DIR/dsync`` (or
``/tmp/dsync-<uid>`` as a fallback). Per-process: the daemon writes a file
``relay-<pid>.sock`` and ``run_backup`` discovers it via a small registry
file ``relay.current``. Windows support — loopback TCP + a per-session
bearer token — is deferred to a hardening PR.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
import os
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

#: Hard cap on a single IPC frame (request OR response body).
MAX_IPC_BODY_SIZE: Final[int] = 1 * 1024 * 1024  # 1 MiB

#: Default IPC base dir (POSIX). Falls back to ``/tmp/dsync-<uid>`` if
#: ``XDG_RUNTIME_DIR`` isn't set.
_DEFAULT_BASE_RELATIVE = "dsync"


def default_ipc_dir() -> Path:
    """Return the directory where IPC sockets live."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        directory = Path(base) / _DEFAULT_BASE_RELATIVE
    else:
        directory = Path(f"/tmp/dsync-{os.getuid()}")
    return directory


# ---------------------------------------------------------------------------
# Message models
# ---------------------------------------------------------------------------


class SyncFolderRequest(BaseModel):
    """``run_backup`` → daemon: please sync this folder to this peer."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    op: Literal["sync_folder"] = "sync_folder"
    folder_id: str = Field(min_length=1)
    peer_id: str = Field(min_length=1)


class StatusRequest(BaseModel):
    """``run_backup`` → daemon: is the daemon healthy and registered?"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    op: Literal["status"] = "status"


class IpcResponse(BaseModel):
    """daemon → ``run_backup``: outcome of a request."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["ok", "error"]
    reason: str | None = None


# ---------------------------------------------------------------------------
# Frame I/O
# ---------------------------------------------------------------------------


async def _read_frame(reader: asyncio.StreamReader) -> bytes:
    """Read one length-prefixed frame from ``reader`` and return its body."""
    header = await reader.readexactly(4)
    length = int.from_bytes(header, "big")
    if length > MAX_IPC_BODY_SIZE:
        raise ValueError(f"IPC frame too large: {length} > {MAX_IPC_BODY_SIZE}")
    return await reader.readexactly(length)


async def _write_frame(writer: asyncio.StreamWriter, body: bytes) -> None:
    """Write a length-prefixed frame containing ``body``."""
    if len(body) > MAX_IPC_BODY_SIZE:
        raise ValueError(f"IPC frame too large: {len(body)} > {MAX_IPC_BODY_SIZE}")
    writer.write(len(body).to_bytes(4, "big") + body)
    await writer.drain()


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


#: A handler takes the parsed request body (raw dict) and returns the
#: response body (also a raw dict). Async because handlers will drive QUIC.
RequestHandler = Callable[[dict[str, object]], Awaitable[dict[str, object]]]


class LocalControlServer:
    """Unix-domain-socket server hosted by ``dsync relay connect``.

    Lifecycle::

        server = LocalControlServer(socket_path=path, handler=on_request)
        await server.start()
        # ... daemon runs, request handler invoked per connection ...
        await server.close()

    The socket file is created with mode ``0600`` so only the running user
    can talk to the daemon. A ``relay.current`` pointer file in the same
    directory exposes the active daemon's socket path to ``run_backup``.
    """

    def __init__(
        self,
        *,
        socket_path: Path,
        handler: RequestHandler,
    ) -> None:
        """Configure the server. Use :meth:`start` to bind."""
        self._socket_path = socket_path
        self._handler = handler
        self._server: asyncio.Server | None = None
        self._pointer_path: Path | None = None

    @property
    def socket_path(self) -> Path:
        """The Unix-domain-socket path the server is bound to."""
        return self._socket_path

    async def start(self) -> None:
        """Bind the Unix-domain socket and start serving."""
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        # Stale leftover sockets confuse asyncio's start_unix_server.
        if self._socket_path.exists():
            self._socket_path.unlink()
        self._server = await asyncio.start_unix_server(
            self._on_client,
            path=str(self._socket_path),
        )
        # 0600 — owner-only read/write.
        os.chmod(self._socket_path, 0o600)

        # Publish the socket path so `run_backup` can find us.
        self._pointer_path = self._socket_path.parent / "relay.current"
        self._pointer_path.write_text(str(self._socket_path), encoding="utf-8")
        os.chmod(self._pointer_path, 0o600)

        logger.info("LocalControlServer bound on %s", self._socket_path)

    async def close(self) -> None:
        """Stop the server, unlink the socket and pointer files."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._socket_path.exists():
            self._socket_path.unlink()
        if self._pointer_path is not None and self._pointer_path.exists():
            try:
                # Only clear the pointer if it still points to us.
                if self._pointer_path.read_text().strip() == str(self._socket_path):
                    self._pointer_path.unlink()
            except OSError:
                pass

    async def _on_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            try:
                body = await _read_frame(reader)
            except (asyncio.IncompleteReadError, ValueError) as exc:
                await _write_frame(
                    writer,
                    IpcResponse(status="error", reason=str(exc))
                    .model_dump_json()
                    .encode("utf-8"),
                )
                return

            try:
                request = json.loads(body.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                await _write_frame(
                    writer,
                    IpcResponse(status="error", reason=f"bad JSON: {exc}")
                    .model_dump_json()
                    .encode("utf-8"),
                )
                return

            try:
                response_obj = await self._handler(request)
            except Exception as exc:
                logger.exception("IPC handler raised")
                response_obj = {"status": "error", "reason": str(exc)}

            await _write_frame(
                writer,
                json.dumps(response_obj).encode("utf-8"),
            )
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class LocalControlClient:
    """One-shot client used by ``dsync sync run_backup``."""

    def __init__(self, *, socket_path: Path) -> None:
        """Configure the client. Use :meth:`request` to actually call."""
        self._socket_path = socket_path

    @classmethod
    def discover(cls, *, ipc_dir: Path | None = None) -> LocalControlClient:
        """Locate the running daemon via the ``relay.current`` pointer file.

        Raises:
            FileNotFoundError: If no daemon's pointer file is found.
        """
        directory = ipc_dir or default_ipc_dir()
        pointer = directory / "relay.current"
        if not pointer.is_file():
            raise FileNotFoundError(
                f"no running relay-connect daemon (pointer file missing: {pointer})"
            )
        path = Path(pointer.read_text(encoding="utf-8").strip())
        if not path.exists():
            raise FileNotFoundError(
                f"stale relay.current pointer: {path} does not exist"
            )
        return cls(socket_path=path)

    async def request(self, payload: BaseModel) -> IpcResponse:
        """Send ``payload`` to the daemon and wait for an :class:`IpcResponse`."""
        reader, writer = await asyncio.open_unix_connection(path=str(self._socket_path))
        try:
            await _write_frame(writer, payload.model_dump_json().encode("utf-8"))
            response_body = await _read_frame(reader)
            return IpcResponse.model_validate(json.loads(response_body.decode("utf-8")))
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
