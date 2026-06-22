from __future__ import annotations

from unittest.mock import patch

import pytest

from dsync.crypto.secure_storage import SecureKeyStorage


class _MemoryBackend:
    name = "test-memory"

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._data.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._data[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._data.pop((service, username), None)


@pytest.fixture
def mem_keyring() -> _MemoryBackend:
    backend = _MemoryBackend()
    with patch("dsync.crypto.secure_storage.keyring.get_keyring", return_value=backend):
        yield backend


@pytest.fixture
def storage(mem_keyring: _MemoryBackend) -> SecureKeyStorage:
    return SecureKeyStorage(service_name="dsync-test")
