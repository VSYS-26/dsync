"""Backup-mode session: enforces source → peer, never the reverse.

Policy:
    In backup mode the local node (source) is the only side that may
    *send* file data. The remote node (peer) is the only side that may
    *receive* and write file data to disk. The peer has write-only
    access to the configured destination path and never reads it back
    to the source.

This module is the backup-mode dispatcher. ``file_transfer`` stays a
neutral primitive (send and receive are independent functions, so
future mirror mode can reuse them bidirectionally). ``BackupSession``
wraps that primitive and adds the direction policy:

    * a SOURCE session can only ``send_files``; calling
      ``receive_files`` raises ``DirectionViolationError``.
    * a PEER session can only ``receive_files``; calling
      ``send_files`` raises ``DirectionViolationError``.

This way the policy lives in one named place and is enforced at
runtime the moment any caller tries to flip direction in backup mode.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import TYPE_CHECKING

from dsync.network.file_transfer import recv_file, send_file

if TYPE_CHECKING:
    from pathlib import Path

    from aioquic.quic.connection import QuicConnection


class TransferRole(Enum):
    """Role of the local node in a backup-mode sync.

    SOURCE: holds the authoritative data, sends file frames.
    PEER:   destination, only writes received files; never sends file data.
    """

    SOURCE = "source"
    PEER = "peer"


class DirectionViolationError(RuntimeError):
    """Raised when a caller tries to flip direction in backup mode.

    Examples:
        * a PEER session calling ``send_files``
        * a SOURCE session calling ``receive_files``

    A correctly-behaving backup client never does either; seeing one
    means the calling code is buggy or hostile.
    """


class BackupSession:
    """Backup-mode dispatcher with runtime direction enforcement.

    Holds the local node's :class:`TransferRole` for the session and
    exposes role-checked file I/O methods. Callers obtain a session by
    role and then drive the transfer through it; mismatched calls raise
    :class:`DirectionViolationError` instead of silently doing the
    wrong thing.
    """

    def __init__(self, role: TransferRole) -> None:
        """Initialize with the given transfer role."""
        self._role = role

    @property
    def role(self) -> TransferRole:
        """Return the transfer role for this session."""
        return self._role

    @classmethod
    def as_source(cls) -> BackupSession:
        """Open a backup session as the SOURCE (sends data)."""
        return cls(TransferRole.SOURCE)

    @classmethod
    def as_peer(cls) -> BackupSession:
        """Open a backup session as the PEER (writes received data)."""
        return cls(TransferRole.PEER)

    async def send_files(
        self,
        writer: asyncio.StreamWriter,
        reader: asyncio.StreamReader,
        files: tuple[Path, ...],
        root: Path,
        quic: QuicConnection | None = None,
    ) -> None:
        """Send ``files`` to the peer with paths relativized against ``root``.

        Args:
            writer: Authenticated asyncio stream from the connection setup.
            reader: Authenticated asyncio stream from the connection setup.
            files: Source files to transmit.
            root: Folder root the paths should be relativized against.
            quic: Underlying QUIC connection, forwarded to ``send_file`` to
                pace the transfer to real network delivery.

        Raises:
            DirectionViolationError: If this session is not the SOURCE.
                In backup mode only the source may send file data.
        """
        if self._role is not TransferRole.SOURCE:
            raise DirectionViolationError(
                "Peer attempted to send files in backup mode; "
                "only the source may send (source → peer is the only legal direction)."
            )
        for src in files:
            await send_file(writer, reader, src, root, quic)

    async def receive_files(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, recv_dir: Path
    ) -> None:
        """Receive files from the source into ``recv_dir`` until EOF.

        Raises:
            DirectionViolationError: If this session is not the PEER.
                In backup mode only the peer may receive file data.
        """
        if self._role is not TransferRole.PEER:
            raise DirectionViolationError(
                "Source attempted to receive files in backup mode; "
                "only the peer may receive (source → peer is the only legal direction)."
            )
        while True:
            try:
                await recv_file(reader, writer, recv_dir)
            except asyncio.IncompleteReadError as e:
                if e.partial:
                    raise
                return
