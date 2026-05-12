"""Secure key storage using platform-specific keystores.

Uses system keystore/credential manager via keyring library.

Supported platforms:
- macOS: Keychain
- Windows: Credential Locker
- Linux: Secret Service/KWallet
"""

from __future__ import annotations

import warnings

import keyring
from keyring.errors import KeyringError, PasswordSetError

SERVICE_NAME = "dsync"
PRIVATE_KEY_USERNAME = "private_key"
PUBLIC_KEY_USERNAME = "public_key"


class KeyExistsError(Exception):
    """Raised when trying to overwrite existing key without force."""


class SecureKeyStorage:
    """Manages secure key storage using system keystores."""

    def __init__(self, service_name: str = SERVICE_NAME) -> None:
        """Initialize secure storage with service name."""
        self.service_name = service_name
        self._keyring = keyring.get_keyring()

    def key_exists(self, username: str) -> bool:
        """Check if key exists in keystore."""
        try:
            return self._keyring.get_password(self.service_name, username) is not None
        except KeyringError:
            return False

    def store_private_key(
        self, private_key_pem: str, force: bool = False, warn_on_existing: bool = True
    ) -> None:
        """Store private key in system keystore.

        Args:
            private_key_pem: PEM-encoded private key
            force: Overwrite existing key if True
            warn_on_existing: Show warning when key exists

        Raises:
            KeyExistsError: Key exists and force=False
            KeyringError: Keyring backend failed
        """
        if not force and self.key_exists(PRIVATE_KEY_USERNAME):
            raise KeyExistsError(
                "Private key already exists in keystore. "
                "Use force=True to overwrite or delete the existing key first."
            )

        if warn_on_existing and self.key_exists(PRIVATE_KEY_USERNAME):
            warnings.warn(
                "Overwriting existing private key in keystore. "
                "This will invalidate any existing trust relationships.",
                UserWarning,
                stacklevel=2,
            )

        try:
            self._keyring.set_password(self.service_name, PRIVATE_KEY_USERNAME, private_key_pem)
        except (PasswordSetError, KeyringError) as e:
            raise KeyringError(f"Failed to store private key: {e}") from e

    def store_public_key(
        self, public_key_pem: str, force: bool = False, warn_on_existing: bool = True
    ) -> None:
        """Store public key in system keystore.

        Args:
            public_key_pem: PEM-encoded public key
            force: Overwrite existing key if True
            warn_on_existing: Show warning when key exists

        Raises:
            KeyExistsError: Key exists and force=False
            KeyringError: Keyring backend failed
        """
        if not force and self.key_exists(PUBLIC_KEY_USERNAME):
            raise KeyExistsError(
                "Public key already exists in keystore. "
                "Use force=True to overwrite or delete the existing key first."
            )

        if warn_on_existing and self.key_exists(PUBLIC_KEY_USERNAME):
            warnings.warn("Overwriting existing public key in keystore.", UserWarning, stacklevel=2)

        try:
            self._keyring.set_password(self.service_name, PUBLIC_KEY_USERNAME, public_key_pem)
        except (PasswordSetError, KeyringError) as e:
            raise KeyringError(f"Failed to store public key: {e}") from e

    def store_keypair(
        self,
        private_key_pem: str,
        public_key_pem: str,
        force: bool = False,
        warn_on_existing: bool = True,
    ) -> None:
        """Store both private and public keys.

        Args:
            private_key_pem: PEM-encoded private key
            public_key_pem: PEM-encoded public key
            force: Overwrite existing keys if True
            warn_on_existing: Show warning when keys exist

        Raises:
            KeyExistsError: Any key exists and force=False
            KeyringError: Keyring backend failed
        """
        # Check existence first to fail fast
        if not force:
            if self.key_exists(PRIVATE_KEY_USERNAME):
                raise KeyExistsError("Private key already exists in keystore")
            if self.key_exists(PUBLIC_KEY_USERNAME):
                raise KeyExistsError("Public key already exists in keystore")

        # Store both keys
        self.store_private_key(private_key_pem, force, warn_on_existing)
        self.store_public_key(public_key_pem, force, warn_on_existing)

    def get_private_key(self) -> str | None:
        """Get private key from keystore.

        Returns:
            PEM-encoded private key, or None

        Raises:
            KeyringError: Keyring backend failed
        """
        try:
            return self._keyring.get_password(self.service_name, PRIVATE_KEY_USERNAME)
        except KeyringError as e:
            raise KeyringError(f"Failed to retrieve private key: {e}") from e

    def get_public_key(self) -> str | None:
        """Get public key from keystore.

        Returns:
            PEM-encoded public key, or None

        Raises:
            KeyringError: Keyring backend failed
        """
        try:
            return self._keyring.get_password(self.service_name, PUBLIC_KEY_USERNAME)
        except KeyringError as e:
            raise KeyringError(f"Failed to retrieve public key: {e}") from e

    def get_keypair(self) -> tuple[str | None, str | None]:
        """Get both private and public keys from keystore.

        Returns:
            (private_key_pem, public_key_pem), either may be None

        Raises:
            KeyringError: Keyring backend failed
        """
        private_key = self.get_private_key()
        public_key = self.get_public_key()
        return private_key, public_key

    def delete_private_key(self) -> bool:
        """Delete private key from keystore.

        Returns:
            True if deleted, False if didn't exist

        Raises:
            KeyringError: Keyring backend failed
        """
        if not self.key_exists(PRIVATE_KEY_USERNAME):
            return False

        try:
            self._keyring.delete_password(self.service_name, PRIVATE_KEY_USERNAME)
        except KeyringError as e:
            raise KeyringError(f"Failed to delete private key: {e}") from e
        else:
            return True

    def delete_public_key(self) -> bool:
        """Delete public key from keystore.

        Returns:
            True if deleted, False if didn't exist

        Raises:
            KeyringError: Keyring backend failed
        """
        if not self.key_exists(PUBLIC_KEY_USERNAME):
            return False

        try:
            self._keyring.delete_password(self.service_name, PUBLIC_KEY_USERNAME)
        except KeyringError as e:
            raise KeyringError(f"Failed to delete public key: {e}") from e
        else:
            return True

    def delete_keypair(self) -> tuple[bool, bool]:
        """Delete both private and public keys from keystore.

        Returns:
            (private_deleted, public_deleted) success flags

        Raises:
            KeyringError: Keyring backend failed
        """
        private_deleted = self.delete_private_key()
        public_deleted = self.delete_public_key()
        return private_deleted, public_deleted

    def has_keypair(self) -> bool:
        """Check if both private and public keys exist.

        Returns:
            True if both keys exist, False otherwise
        """
        return self.key_exists(PRIVATE_KEY_USERNAME) and self.key_exists(PUBLIC_KEY_USERNAME)

    def get_backend_info(self) -> dict[str, str]:
        """Get keyring backend information.

        Returns:
            Dictionary with backend info
        """
        return {
            "name": self._keyring.name,
            "class": self._keyring.__class__.__name__,
            "module": self._keyring.__class__.__module__,
        }
