"""Type stubs for keyring.errors module."""

class KeyringError(Exception):
    """Base exception for keyring errors."""

class PasswordSetError(KeyringError):
    """Exception raised when password setting fails."""

__all__ = ["KeyringError", "PasswordSetError"]
