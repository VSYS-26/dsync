from unittest.mock import patch

from keyring.errors import KeyringError
import pytest

from dsync.crypto.secure_storage import (
    KeyExistsError,
    SecureKeyStorage,
)


def test_store_and_retrieve_private_key(storage: SecureKeyStorage) -> None:
    storage.store_private_key("private-pem-data")
    assert storage.get_private_key() == "private-pem-data"


def test_store_and_retrieve_public_key(storage: SecureKeyStorage) -> None:
    storage.store_public_key("public-pem-data")
    assert storage.get_public_key() == "public-pem-data"


def test_get_missing_key_returns_none(storage: SecureKeyStorage) -> None:
    assert storage.get_private_key() is None
    assert storage.get_public_key() is None


def test_key_exists_false_when_absent(storage: SecureKeyStorage) -> None:
    assert storage.key_exists("private_key") is False
    assert storage.key_exists("public_key") is False


def test_key_exists_true_after_store(storage: SecureKeyStorage) -> None:
    storage.store_private_key("priv")
    assert storage.key_exists("private_key") is True


def test_store_private_key_duplicate_raises(storage: SecureKeyStorage) -> None:
    storage.store_private_key("first")
    with pytest.raises(KeyExistsError):
        storage.store_private_key("second")


def test_store_private_key_force_overwrites(storage: SecureKeyStorage) -> None:
    storage.store_private_key("first")
    storage.store_private_key("second", force=True)
    assert storage.get_private_key() == "second"


def test_store_keypair_raises_if_private_exists(storage: SecureKeyStorage) -> None:
    storage.store_private_key("priv")
    with pytest.raises(KeyExistsError):
        storage.store_keypair("priv", "pub")


def test_store_keypair_raises_if_public_exists(storage: SecureKeyStorage) -> None:
    storage.store_public_key("pub")
    with pytest.raises(KeyExistsError):
        storage.store_keypair("priv", "pub")


def test_store_keypair_force_succeeds(storage: SecureKeyStorage) -> None:
    storage.store_keypair("priv-1", "pub-1")
    storage.store_keypair("priv-2", "pub-2", force=True)
    priv, pub = storage.get_keypair()
    assert priv == "priv-2"
    assert pub == "pub-2"


def test_get_keypair_returns_both(storage: SecureKeyStorage) -> None:
    storage.store_keypair("my-priv", "my-pub")
    priv, pub = storage.get_keypair()
    assert priv == "my-priv"
    assert pub == "my-pub"


def test_delete_private_key(storage: SecureKeyStorage) -> None:
    storage.store_private_key("priv")
    deleted = storage.delete_private_key()
    assert deleted is True
    assert storage.get_private_key() is None


def test_delete_missing_key_returns_false(storage: SecureKeyStorage) -> None:
    assert storage.delete_private_key() is False
    assert storage.delete_public_key() is False


def test_delete_keypair(storage: SecureKeyStorage) -> None:
    storage.store_keypair("priv", "pub")
    priv_del, pub_del = storage.delete_keypair()
    assert priv_del is True
    assert pub_del is True
    assert not storage.has_keypair()


def test_has_keypair_false_when_empty(storage: SecureKeyStorage) -> None:
    assert storage.has_keypair() is False


def test_has_keypair_false_when_only_one(storage: SecureKeyStorage) -> None:
    storage.store_private_key("priv")
    assert storage.has_keypair() is False


def test_has_keypair_true_when_both(storage: SecureKeyStorage) -> None:
    storage.store_keypair("priv", "pub")
    assert storage.has_keypair() is True


def test_get_backend_info_returns_dict(storage: SecureKeyStorage) -> None:
    info = storage.get_backend_info()
    assert "name" in info
    assert "class" in info


# ── KeyringError propagation paths ───────────────────────────────────────────


class _FailingBackend:
    name = "test-failing"

    def __init__(self, fail_on: set[str]) -> None:
        self._data: dict[tuple[str, str], str] = {}
        self._fail_on = fail_on

    def get_password(self, service: str, username: str) -> str | None:
        if "get" in self._fail_on:
            raise KeyringError("backend get failed")
        return self._data.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if "set" in self._fail_on:
            raise KeyringError("backend set failed")
        self._data[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if "delete" in self._fail_on:
            raise KeyringError("backend delete failed")
        self._data.pop((service, username), None)


def _failing_storage(fail_on: set[str]) -> SecureKeyStorage:
    backend = _FailingBackend(fail_on)
    with patch("dsync.crypto.secure_storage.keyring.get_keyring", return_value=backend):
        return SecureKeyStorage(service_name="dsync-test")


def test_key_exists_keyring_error_returns_false() -> None:
    s = _failing_storage({"get"})
    assert s.key_exists("private_key") is False


def test_store_private_key_set_error_raises_keyring_error() -> None:
    s = _failing_storage({"set"})
    with pytest.raises(KeyringError):
        s.store_private_key("priv")


def test_store_public_key_set_error_raises_keyring_error() -> None:
    s = _failing_storage({"set"})
    with pytest.raises(KeyringError):
        s.store_public_key("pub")


def test_get_private_key_get_error_raises_keyring_error() -> None:
    s = _failing_storage({"get"})
    with pytest.raises(KeyringError):
        s.get_private_key()


def test_get_public_key_get_error_raises_keyring_error() -> None:
    s = _failing_storage({"get"})
    with pytest.raises(KeyringError):
        s.get_public_key()


def test_delete_private_key_delete_error_raises_keyring_error() -> None:
    backend = _FailingBackend(set())
    backend._data[("dsync-test", "private_key")] = "priv"
    backend._fail_on = {"delete"}
    with patch("dsync.crypto.secure_storage.keyring.get_keyring", return_value=backend):
        s = SecureKeyStorage(service_name="dsync-test")
    with pytest.raises(KeyringError):
        s.delete_private_key()


def test_delete_public_key_delete_error_raises_keyring_error() -> None:
    backend = _FailingBackend(set())
    backend._data[("dsync-test", "public_key")] = "pub"
    backend._fail_on = {"delete"}
    with patch("dsync.crypto.secure_storage.keyring.get_keyring", return_value=backend):
        s = SecureKeyStorage(service_name="dsync-test")
    with pytest.raises(KeyringError):
        s.delete_public_key()
