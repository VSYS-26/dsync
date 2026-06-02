"""Type stubs for keyring module."""

class KeyringError(Exception):
    """Base exception for keyring errors."""

class PasswordSetError(KeyringError):
    """Exception raised when password setting fails."""

class KeyringBackend:
    """Base class for keyring backends."""

    name: str

    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...

def get_keyring() -> KeyringBackend: ...
