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


class ConfigConflictError(Exception):
    """Raised when circular sync configuration is detected between peers."""
