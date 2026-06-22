import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
import pytest

from dsync.crypto.keys import (
    delete_stored_keypair,
    generate_and_store_keypair_securely,
    generate_keypair,
    has_stored_keypair,
    is_valid_fingerprint,
    load_keypair,
    load_keypair_securely,
    migrate_keys_to_secure_storage,
    public_key_fingerprint,
    save_keypair,
)
from dsync.crypto.secure_storage import KeyExistsError


def test_generate_keypair_returns_pem_bytes() -> None:
    priv, pub = generate_keypair()
    assert priv.startswith(b"-----BEGIN PRIVATE KEY-----")
    assert pub.startswith(b"-----BEGIN PUBLIC KEY-----")


def test_generate_keypair_is_ed25519() -> None:
    priv_pem, pub_pem = generate_keypair()
    priv = serialization.load_pem_private_key(priv_pem, password=None)
    pub = serialization.load_pem_public_key(pub_pem)
    assert isinstance(priv, Ed25519PrivateKey)
    assert isinstance(pub, Ed25519PublicKey)


def test_generate_keypair_unique() -> None:
    _, pub_a = generate_keypair()
    _, pub_b = generate_keypair()
    assert pub_a != pub_b


def test_fingerprint_format_hex() -> None:
    _, pub_pem = generate_keypair()
    fp = public_key_fingerprint(pub_pem)
    assert fp.startswith("hex-")
    hex_part = fp[4:]
    assert len(hex_part) == 64
    assert all(c in "0123456789abcdef" for c in hex_part)


def test_fingerprint_format_base64url() -> None:
    _, pub_pem = generate_keypair()
    fp = public_key_fingerprint(pub_pem, fmt="base64url")
    assert fp.startswith("b64u-")
    b64_part = fp[5:]
    padded = b64_part + ("=" * (-len(b64_part) % 4))
    decoded = base64.urlsafe_b64decode(padded.encode())
    assert len(decoded) == 32


def test_fingerprint_deterministic() -> None:
    _, pub_pem = generate_keypair()
    assert public_key_fingerprint(pub_pem) == public_key_fingerprint(pub_pem)


def test_fingerprint_differs_for_different_keys() -> None:
    _, pub_a = generate_keypair()
    _, pub_b = generate_keypair()
    assert public_key_fingerprint(pub_a) != public_key_fingerprint(pub_b)


def test_fingerprint_non_ed25519_raises() -> None:
    from cryptography.hazmat.primitives.asymmetric import rsa

    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_pub_pem = rsa_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    with pytest.raises(TypeError, match="Ed25519"):
        public_key_fingerprint(rsa_pub_pem)


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    priv_pem, pub_pem = generate_keypair()
    save_keypair(priv_pem, pub_pem, tmp_path / "priv.pem", tmp_path / "pub.pem")
    loaded_priv, loaded_pub = load_keypair(tmp_path / "priv.pem", tmp_path / "pub.pem")
    assert loaded_priv == priv_pem
    assert loaded_pub == pub_pem


@pytest.mark.parametrize(
    ("fp", "expected"),
    [
        ("hex-" + "a" * 64, True),
        ("hex-" + "f" * 64, True),
        ("a" * 64, True),
        ("0" * 64, True),
        ("hex-" + "a" * 63, False),
        ("hex-" + "a" * 65, False),
        ("not-a-fingerprint", False),
        ("", False),
        ("a" * 32, False),
        ("b64u-tooshort", False),
        ("hex-" + "z" * 64, False),
    ],
)
def test_is_valid_fingerprint(fp: str, expected: bool) -> None:
    assert is_valid_fingerprint(fp) is expected


def test_is_valid_fingerprint_b64u_valid() -> None:
    digest = b"\xab" * 32
    b64 = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert is_valid_fingerprint(f"b64u-{b64}") is True


def test_is_valid_fingerprint_b64u_non_ascii_raises_and_returns_false() -> None:
    assert is_valid_fingerprint("b64u-\xff\xfe") is False


def test_fingerprint_unsupported_format_raises() -> None:
    _, pub_pem = generate_keypair()
    with pytest.raises(ValueError, match="Unsupported"):
        public_key_fingerprint(pub_pem, fmt="base64")  # type: ignore[arg-type]


def test_default_key_paths_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from dsync.crypto.keys import default_key_paths

    monkeypatch.setenv("APPDATA", "C:\\Users\\user\\AppData\\Roaming")
    monkeypatch.setattr("platform.system", lambda: "Windows")
    priv, _pub = default_key_paths()
    assert "dsync" in str(priv)
    assert priv.suffix == ".pem"


def test_default_key_paths_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    from dsync.crypto.keys import default_key_paths

    monkeypatch.setattr("platform.system", lambda: "Linux")
    priv, _pub = default_key_paths()
    assert ".config" in str(priv)
    assert priv.suffix == ".pem"


# ── secure wrapper functions ──────────────────────────────────────────────────


def test_generate_and_store_returns_pem_strings(mem_keyring) -> None:
    priv, pub = generate_and_store_keypair_securely(service_name="dsync-test")
    assert priv.startswith("-----BEGIN PRIVATE KEY-----")
    assert pub.startswith("-----BEGIN PUBLIC KEY-----")


def test_generate_and_store_persists_to_keyring(mem_keyring) -> None:
    generate_and_store_keypair_securely(service_name="dsync-test")
    assert ("dsync-test", "private_key") in mem_keyring._data
    assert ("dsync-test", "public_key") in mem_keyring._data


def test_generate_and_store_duplicate_raises(mem_keyring) -> None:
    generate_and_store_keypair_securely(service_name="dsync-test")
    with pytest.raises(KeyExistsError):
        generate_and_store_keypair_securely(service_name="dsync-test")


def test_generate_and_store_force_overwrites(mem_keyring) -> None:
    priv1, _ = generate_and_store_keypair_securely(service_name="dsync-test")
    priv2, _ = generate_and_store_keypair_securely(service_name="dsync-test", force=True)
    assert priv1 != priv2


def test_load_keypair_securely_returns_bytes(mem_keyring) -> None:
    generate_and_store_keypair_securely(service_name="dsync-test")
    result = load_keypair_securely(service_name="dsync-test")
    assert result is not None
    priv, pub = result
    assert isinstance(priv, bytes)
    assert isinstance(pub, bytes)


def test_load_keypair_securely_none_when_absent(mem_keyring) -> None:
    result = load_keypair_securely(service_name="dsync-test")
    assert result is None


def test_has_stored_keypair_false_when_empty(mem_keyring) -> None:
    assert has_stored_keypair(service_name="dsync-test") is False


def test_has_stored_keypair_true_after_generate(mem_keyring) -> None:
    generate_and_store_keypair_securely(service_name="dsync-test")
    assert has_stored_keypair(service_name="dsync-test") is True


def test_delete_stored_keypair_removes_keys(mem_keyring) -> None:
    generate_and_store_keypair_securely(service_name="dsync-test")
    priv_del, pub_del = delete_stored_keypair(service_name="dsync-test")
    assert priv_del is True
    assert pub_del is True
    assert has_stored_keypair(service_name="dsync-test") is False


def test_delete_stored_keypair_when_absent(mem_keyring) -> None:
    priv_del, pub_del = delete_stored_keypair(service_name="dsync-test")
    assert priv_del is False
    assert pub_del is False


def test_migrate_keys_copies_to_keyring(mem_keyring, tmp_path: Path) -> None:
    priv_pem, pub_pem = generate_keypair()
    priv_path = tmp_path / "priv.pem"
    pub_path = tmp_path / "pub.pem"
    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)

    priv_migrated, pub_migrated = migrate_keys_to_secure_storage(
        private_path=priv_path,
        public_path=pub_path,
        service_name="dsync-test",
    )

    assert priv_migrated is True
    assert pub_migrated is True
    assert has_stored_keypair(service_name="dsync-test") is True


def test_migrate_missing_files_returns_false(mem_keyring, tmp_path: Path) -> None:
    priv_migrated, pub_migrated = migrate_keys_to_secure_storage(
        private_path=tmp_path / "nonexistent_priv.pem",
        public_path=tmp_path / "nonexistent_pub.pem",
        service_name="dsync-test",
    )
    assert priv_migrated is False
    assert pub_migrated is False


def test_migrate_skips_when_keys_already_exist(mem_keyring, tmp_path: Path) -> None:
    priv_pem, pub_pem = generate_keypair()
    priv_path = tmp_path / "priv.pem"
    pub_path = tmp_path / "pub.pem"
    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)

    migrate_keys_to_secure_storage(
        private_path=priv_path, public_path=pub_path, service_name="dsync-test"
    )
    priv_migrated, pub_migrated = migrate_keys_to_secure_storage(
        private_path=priv_path, public_path=pub_path, service_name="dsync-test"
    )
    assert priv_migrated is False
    assert pub_migrated is False
