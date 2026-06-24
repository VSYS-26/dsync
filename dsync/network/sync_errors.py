"""Wire-level error categories exchanged between peers during sync.

When one side aborts (auth failure, config conflict, bad file, disk full, ...)
it sends a structured :class:`SyncError` to the peer *before* closing the
connection. The peer then prints the category + message instead of guessing
from a bare "connection closed".

Categories:
    AUTH      - TLS / fingerprint / signature problems
    CONFIG    - folder config disagreement (mode, recursive, whitelist, ...)
    INTEGRITY - hash mismatch, corrupt zip, frame contract violation
    IO        - source missing, permission denied, disk full
    PROTOCOL  - unexpected message type or order, oversize payload, timeout
    INTERNAL  - bug on the sender's side (uncaught exception, etc.)

Codes are short, stable strings so they can be matched in logs and tests
without depending on the human-readable message.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    import asyncio

    from dsync.network.quic_core import MsgType as _MsgType  # noqa: F401


class ErrorCategory(StrEnum):
    """High-level grouping of sync failures shown to the user."""

    AUTH = "auth"
    CONFIG = "config"
    INTEGRITY = "integrity"
    IO = "io"
    PROTOCOL = "protocol"
    INTERNAL = "internal"


class ErrorCode(StrEnum):
    """Stable identifier for a specific failure mode."""

    # AUTH
    UNKNOWN_DEVICE = "auth.unknown_device"
    BAD_SIGNATURE = "auth.bad_signature"
    UNSAFE_PEER_ID = "auth.unsafe_peer_id"
    NON_RSA_KEY = "auth.non_rsa_key"

    # CONFIG
    FOLDER_NOT_CONFIGURED = "config.folder_not_configured"
    MODE_MISMATCH = "config.mode_mismatch"
    RECURSIVE_MISMATCH = "config.recursive_mismatch"
    DEVICE_NOT_WHITELISTED = "config.device_not_whitelisted"
    BIDIRECTIONAL_CONFLICT = "config.bidirectional_conflict"
    MIRROR_CONFLICT = "config.mirror_conflict"
    MULTIPLE_FOLDERS = "config.multiple_folders"
    INVALID_CONFIG_PAYLOAD = "config.invalid_payload"

    # INTEGRITY
    HASH_MISMATCH = "integrity.hash_mismatch"
    CHUNK_OVERRUN = "integrity.chunk_overrun"
    EMPTY_CHUNK = "integrity.empty_chunk"
    BAD_ZIP = "integrity.bad_zip"
    PATH_TRAVERSAL = "integrity.path_traversal"

    # IO
    SOURCE_MISSING = "io.source_missing"
    PERMISSION_DENIED = "io.permission_denied"
    DISK_FULL = "io.disk_full"

    # PROTOCOL
    UNEXPECTED_FRAME = "protocol.unexpected_frame"
    OVERSIZE_PAYLOAD = "protocol.oversize_payload"
    UNKNOWN_FRAME_TYPE = "protocol.unknown_frame_type"

    # INTERNAL
    INTERNAL_ERROR = "internal.error"

    @property
    def category(self) -> ErrorCategory:
        """Return the high-level :class:`ErrorCategory` for this code."""
        return ErrorCategory(self.value.split(".", 1)[0])


@dataclass(frozen=True)
class SyncError:
    """Structured error sent over the wire before the sender aborts.

    Attributes:
        code: Stable :class:`ErrorCode` identifying the failure mode.
        message: Human-readable detail (paths, sizes, peer ids — never secrets).
    """

    code: ErrorCode
    message: str

    @property
    def category(self) -> ErrorCategory:
        """Convenience accessor for the code's category."""
        return self.code.category

    def to_yaml(self) -> bytes:
        """Serialize to a YAML byte payload for the ERROR frame."""
        return yaml.safe_dump({"code": self.code.value, "message": self.message}).encode("utf-8")

    @classmethod
    def from_yaml(cls, data: bytes) -> SyncError:
        """Parse a YAML byte payload received from a peer.

        Unknown codes are coerced to :attr:`ErrorCode.INTERNAL_ERROR` so a
        newer peer can still report something useful to an older one.
        """
        raw = yaml.safe_load(data.decode("utf-8")) or {}
        raw_code = raw.get("code", ErrorCode.INTERNAL_ERROR.value)
        try:
            code = ErrorCode(raw_code)
        except ValueError:
            code = ErrorCode.INTERNAL_ERROR
        return cls(code=code, message=str(raw.get("message", "")))

    def format(self) -> str:
        """Render as a single user-facing line: ``[CATEGORY:code] message``."""
        return f"[{self.category.value}:{self.code.value}] {self.message}"


_MAX_ERROR_SIZE = 4 * 1024


async def notify_peer(writer: asyncio.StreamWriter, error: SyncError) -> None:
    """Best-effort send of an ERROR frame to the peer."""
    from dsync.network.quic_core import MsgType, async_send_msg

    payload = error.to_yaml()
    if len(payload) > _MAX_ERROR_SIZE:
        payload = payload[:_MAX_ERROR_SIZE]
    try:
        await async_send_msg(writer, MsgType.ERROR, payload)
    except (ConnectionError, OSError):
        return


class PeerReportedError(Exception):
    """Raised locally after we receive an ERROR frame from the peer.

    Carries the original :class:`SyncError` so the CLI layer can show the
    peer's category and message verbatim.
    """

    def __init__(self, sync_error: SyncError) -> None:
        """Wrap ``sync_error`` and use its formatted form as the message."""
        super().__init__(sync_error.format())
        self.sync_error = sync_error
