from pathlib import Path

from pydantic import ValidationError
import pytest

from dsync.config import DevicesConfig, TrustedDevice


def _fp(char: str = "a") -> str:
    return "hex-" + char * 64


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    config = DevicesConfig.load(tmp_path)
    assert config.trusted_devices == []


def test_load_valid_yaml(tmp_path: Path) -> None:
    (tmp_path / DevicesConfig.FILENAME).write_text(
        f"trusted_devices:\n  - id: dev-a\n    fingerprint: {_fp()}\n"
    )
    config = DevicesConfig.load(tmp_path)
    assert len(config.trusted_devices) == 1
    assert config.trusted_devices[0].id == "dev-a"
    assert config.trusted_devices[0].fingerprint == _fp()


def test_duplicate_id_raises() -> None:
    with pytest.raises(ValidationError, match="duplicate id"):
        DevicesConfig(
            trusted_devices=[
                TrustedDevice(id="same", fingerprint=_fp("a")),
                TrustedDevice(id="same", fingerprint=_fp("b")),
            ]
        )


def test_duplicate_fingerprint_raises() -> None:
    with pytest.raises(ValidationError, match="duplicate fingerprint"):
        DevicesConfig(
            trusted_devices=[
                TrustedDevice(id="dev-a", fingerprint=_fp("a")),
                TrustedDevice(id="dev-b", fingerprint=_fp("a")),
            ]
        )


def test_hex_fingerprint_accepted() -> None:
    device = TrustedDevice(id="d", fingerprint="hex-" + "f" * 64)
    assert device.fingerprint == "hex-" + "f" * 64


def test_b64u_fingerprint_accepted() -> None:
    import base64

    digest = b"\xab" * 32
    b64 = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    device = TrustedDevice(id="d", fingerprint=f"b64u-{b64}")
    assert device.fingerprint.startswith("b64u-")


def test_raw_hex_fingerprint_accepted() -> None:
    device = TrustedDevice(id="d", fingerprint="a" * 64)
    assert len(device.fingerprint) == 64


def test_empty_trusted_devices_allowed() -> None:
    config = DevicesConfig(trusted_devices=[])
    assert config.trusted_devices == []


def test_round_trip_yaml(tmp_path: Path) -> None:
    original = DevicesConfig(
        trusted_devices=[
            TrustedDevice(id="dev-a", fingerprint=_fp("a")),
            TrustedDevice(id="dev-b", fingerprint=_fp("b")),
        ]
    )
    original.save(tmp_path)
    loaded = DevicesConfig.load(tmp_path)
    assert loaded == original


def test_save_raises_if_file_exists(tmp_path: Path) -> None:
    config = DevicesConfig()
    config.save(tmp_path)
    with pytest.raises(FileExistsError):
        config.save(tmp_path, overwrite=False)


def test_save_overwrite_succeeds(tmp_path: Path) -> None:
    config = DevicesConfig()
    config.save(tmp_path)
    config.save(tmp_path, overwrite=True)


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        DevicesConfig.model_validate({"trusted_devices": [], "extra": "bad"})
