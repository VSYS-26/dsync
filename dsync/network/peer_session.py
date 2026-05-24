"""Direct peer-to-peer file-sync session over a QUIC connection.

Once both peers have established a direct QUIC connection (typically via
the relay-brokered hole-punch from :mod:`dsync.network.hole_punch`), they
open a single bidirectional stream and run mutual authentication
followed by a ``BackupSession``-driven file transfer.

The session-level framing uses the ``MsgType`` / ``async_send_msg`` /
``async_recv_msg`` primitives from :mod:`dsync.network.quic_core`.
aioquic's per-stream ``StreamReader`` / ``StreamWriter`` are
duck-compatible with ``asyncio``, so the ``send_file`` / ``recv_file``
and ``BackupSession`` modules work over QUIC unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from dsync.network.backup_direction import BackupSession, TransferRole
from dsync.network.errors import PeerAuthError
from dsync.network.peer_auth import (
    extract_spki,
    fingerprint_from_spki,
    load_rsa_private_key,
    pack_auth_payload,
    sign_channel_binding,
    unpack_auth_payload,
    verify_signature,
)
from dsync.network.quic_core import (
    MsgType,
    async_recv_auth_msg,
    async_send_msg,
    get_quic_channel_binding,
)

if TYPE_CHECKING:
    import asyncio

    from aioquic.quic.connection import QuicConnection

    from dsync.config import FolderEntry
    from dsync.state import AppState

logger = logging.getLogger(__name__)


class PeerSession:
    """Run one direct peer-to-peer sync session on a QUIC connection.

    Lifecycle::

        session = PeerSession.as_source(state=state, folder=folder, ...)
        # ... establish QUIC connection (hole-punched or direct) ...
        reader, writer = await protocol.create_stream()
        peer_id = await session.run(reader, writer, protocol._quic)

    On the receiving side the caller waits for the dialer to open the
    session stream (e.g. via a ``stream_handler`` Future) and then calls
    :meth:`run` with that reader/writer pair.

    The session enforces:

    * Peer's fingerprint is in ``state.devices.trusted_devices``.
    * Peer's RSA signature over the QUIC channel-binding bytes verifies.
    * Direction policy via ``BackupSession``: source sends, peer receives;
      a wrong-way call raises ``DirectionViolationError``.
    """

    def __init__(
        self,
        *,
        role: TransferRole,
        cert_path: Path | str,
        key_path: Path | str,
        state: AppState,
        folder: FolderEntry | None = None,
        recv_dir: Path | None = None,
    ) -> None:
        """Build a session in either SOURCE or PEER role.

        Args:
            role: ``TransferRole.SOURCE`` (sends files) or ``TransferRole.PEER``
                (writes files into ``recv_dir``).
            cert_path: Path to the local TLS certificate (PEM).
            key_path: Path to the local RSA-2048 private key (PEM).
            state: Loaded ``AppState`` — used for ``devices`` trust lookup.
            folder: Required for SOURCE; the folder to enumerate and send.
            recv_dir: Required for PEER; destination directory. Files land
                in ``recv_dir/<peer_id>/<basename>`` to keep peers isolated.
        """
        if role is TransferRole.SOURCE and folder is None:
            raise ValueError("SOURCE role requires a folder to send")
        if role is TransferRole.PEER and recv_dir is None:
            raise ValueError("PEER role requires recv_dir to write files into")

        self._role = role
        self._state = state
        self._folder = folder
        self._recv_dir = recv_dir
        self._cert_path = cert_path
        self._key_path = key_path

        self._private_key = load_rsa_private_key(key_path)
        self._own_spki = extract_spki(self._private_key)
        self._own_fingerprint = fingerprint_from_spki(self._own_spki)
        self._trusted_by_fp: dict[str, str] = {
            device.fingerprint: device.id for device in state.devices.trusted_devices
        }

    @classmethod
    def as_source(
        cls,
        *,
        cert_path: Path | str,
        key_path: Path | str,
        state: AppState,
        folder: FolderEntry,
    ) -> PeerSession:
        """Build a source-side session ready to send the configured folder."""
        return cls(
            role=TransferRole.SOURCE,
            cert_path=cert_path,
            key_path=key_path,
            state=state,
            folder=folder,
        )

    @classmethod
    def as_peer(
        cls,
        *,
        cert_path: Path | str,
        key_path: Path | str,
        state: AppState,
        recv_dir: Path,
    ) -> PeerSession:
        """Build a peer-side session ready to receive files."""
        return cls(
            role=TransferRole.PEER,
            cert_path=cert_path,
            key_path=key_path,
            state=state,
            recv_dir=recv_dir,
        )

    @property
    def fingerprint(self) -> str:
        """SHA-256 hex fingerprint of this peer's own public key."""
        return self._own_fingerprint

    async def run(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        quic_connection: QuicConnection,
        expected_peer_fingerprint: str | None = None,
    ) -> str:
        """Run the AUTH-then-transfer flow on an open QUIC stream.

        Args:
            reader: Stream reader from ``protocol.create_stream()`` (dialer)
                or from the listener's stream handler.
            writer: The matching stream writer.
            quic_connection: The underlying ``QuicConnection`` — used to
                derive the channel-binding bytes that get signed.
            expected_peer_fingerprint: If set (e.g. coming from a relay
                PUNCH_INFO matched pair), the AUTH check additionally
                rejects any peer whose verified fingerprint differs from
                this value. Defence-in-depth: the peer must be both
                trusted in ``devices.yaml`` *and* match the identity the
                relay claimed to broker.

        Returns:
            The verified ``device.id`` of the remote peer (used for
            per-peer subdirectories on the PEER side).

        Raises:
            PeerAuthError: Peer's fingerprint unknown, signature invalid,
                or mismatch with ``expected_peer_fingerprint``.
        """
        peer_id = await self._authenticate(
            reader, writer, quic_connection, expected_peer_fingerprint
        )

        backup = BackupSession(self._role)
        if self._role is TransferRole.SOURCE:
            assert self._folder is not None  # checked in __init__
            files = _enumerate_folder_files(self._folder)
            logger.info("source sending %d file(s) to %s", len(files), peer_id)
            await backup.send_files(writer, files)
            # Half-close so the receiver sees EOF and exits its loop. Then
            # block on the peer's EOF so the caller may safely close the
            # underlying QUIC connection — without this round-trip the
            # source can close before the last chunks have been delivered.
            writer.write_eof()
            await reader.read()
        else:
            assert self._recv_dir is not None  # checked in __init__
            peer_dir = self._recv_dir / peer_id
            peer_dir.mkdir(parents=True, exist_ok=True)
            logger.info("peer receiving files from %s into %s", peer_id, peer_dir)
            await backup.receive_files(reader, peer_dir)
            # Signal back to the source that all chunks have been processed.
            writer.write_eof()

        return peer_id

    # ---------------------------------------------------------------- private

    async def _authenticate(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        quic_connection: QuicConnection,
        expected_peer_fingerprint: str | None,
    ) -> str:
        """Exchange AUTH frames, verify peer, return their trusted-device id."""
        binding = get_quic_channel_binding(quic_connection)
        own_sig = sign_channel_binding(self._private_key, binding)
        own_payload = pack_auth_payload(self._own_spki, own_sig)

        await async_send_msg(writer, MsgType.AUTH, own_payload)

        peer_payload = await async_recv_auth_msg(reader)
        peer_spki, peer_sig = unpack_auth_payload(peer_payload)
        peer_fp = fingerprint_from_spki(peer_spki)

        if peer_fp not in self._trusted_by_fp:
            raise PeerAuthError(f"Unknown peer fingerprint: {peer_fp}")
        if expected_peer_fingerprint is not None and peer_fp != expected_peer_fingerprint:
            raise PeerAuthError(
                f"Peer fingerprint mismatch: expected {expected_peer_fingerprint}, "
                f"got {peer_fp}"
            )

        try:
            verify_signature(peer_spki, binding, peer_sig)
        except ValueError as exc:
            raise PeerAuthError(f"Peer signature invalid: {exc}") from exc

        peer_id = self._trusted_by_fp[peer_fp]
        # Path-injection defence: the peer-id becomes a directory name on PEER side.
        if "/" in peer_id or "\\" in peer_id or peer_id in {".", ".."}:
            raise PeerAuthError(f"Refusing path-unsafe peer id: {peer_id!r}")
        logger.info("verified peer %s (fp=%s)", peer_id, peer_fp)
        return peer_id


def _enumerate_folder_files(folder: FolderEntry) -> tuple[Path, ...]:
    """List files to send from a configured folder, honoring ``recursive``."""
    folder_path = Path(folder.path)
    if not folder_path.exists():
        raise FileNotFoundError(f"Folder {folder_path} does not exist")
    if not folder_path.is_dir():
        raise NotADirectoryError(f"{folder_path} is not a directory")
    candidates = folder_path.rglob("*") if folder.recursive else folder_path.glob("*")
    return tuple(p for p in candidates if p.is_file())
