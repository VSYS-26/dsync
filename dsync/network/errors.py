"""Custom exceptions raised by the dsync network layer."""


class TransferError(Exception):
    """Base class for transfer-related errors."""


class FrameValidationError(TransferError):
    """Raised when a frame header or length is invalid."""


class ChunkValidationError(TransferError):
    """Raised when a file chunk violates the expected transfer contract."""


class TransferIntegrityError(TransferError):
    """Raised when the received data fails integrity checks."""


class PeerAuthError(Exception):
    """Raised when mutual TLS peer authentication fails."""


class RelayError(Exception):
    """Base class for errors raised by relay control-channel logic."""


class RelayAuthError(RelayError):
    """Raised when peer-to-relay authentication fails."""


class RelayProtocolError(RelayError):
    """Raised when a control-channel frame is malformed or unexpected."""


class ConfigConflictError(Exception):
    """Raised when circular sync configuration is detected between peers."""
