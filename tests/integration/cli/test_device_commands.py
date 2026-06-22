from pathlib import Path

from typer.testing import CliRunner

from dsync.cli import cli
from dsync.config import DevicesConfig

_FP_A = "hex-" + "a" * 64
_FP_B = "hex-" + "b" * 64
_FP_C = "hex-" + "c" * 64


def _invoke(runner: CliRunner, config_dir: Path, *args: str):
    return runner.invoke(cli, ["--config-dir", str(config_dir), *args])


def test_device_add_success(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    result = _invoke(cli_runner, tmp_config_dir, "device", "add", "dev-a", _FP_A)
    assert result.exit_code == 0
    devices = DevicesConfig.load(tmp_config_dir)
    assert any(d.id == "dev-a" and d.fingerprint == _FP_A for d in devices.trusted_devices)


def test_device_add_persisted_to_yaml(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    _invoke(cli_runner, tmp_config_dir, "device", "add", "dev-a", _FP_A)
    yaml_text = (tmp_config_dir / DevicesConfig.FILENAME).read_text()
    assert "dev-a" in yaml_text


def test_device_add_duplicate_id_fails(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    _invoke(cli_runner, tmp_config_dir, "device", "add", "dev-a", _FP_A)
    result = _invoke(cli_runner, tmp_config_dir, "device", "add", "dev-a", _FP_B)
    assert result.exit_code != 0
    devices = DevicesConfig.load(tmp_config_dir)
    assert sum(1 for d in devices.trusted_devices if d.id == "dev-a") == 1


def test_device_add_duplicate_fingerprint_fails(
    cli_runner: CliRunner, tmp_config_dir: Path
) -> None:
    _invoke(cli_runner, tmp_config_dir, "device", "add", "dev-a", _FP_A)
    result = _invoke(cli_runner, tmp_config_dir, "device", "add", "dev-b", _FP_A)
    assert result.exit_code != 0
    devices = DevicesConfig.load(tmp_config_dir)
    assert len(devices.trusted_devices) == 1


def test_device_add_invalid_fingerprint_fails(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    result = _invoke(cli_runner, tmp_config_dir, "device", "add", "dev-a", "bad-fp")
    assert result.exit_code != 0
    devices = DevicesConfig.load(tmp_config_dir)
    assert devices.trusted_devices == []


def test_device_rm_removes_entry(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    _invoke(cli_runner, tmp_config_dir, "device", "add", "dev-a", _FP_A)
    result = _invoke(cli_runner, tmp_config_dir, "device", "rm", "dev-a")
    assert result.exit_code == 0
    devices = DevicesConfig.load(tmp_config_dir)
    assert all(d.id != "dev-a" for d in devices.trusted_devices)


def test_device_rm_nonexistent_shows_error(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    result = _invoke(cli_runner, tmp_config_dir, "device", "rm", "nonexistent")
    assert result.exit_code == 0
    assert "not configured" in result.output or "nonexistent" in result.output


def test_device_list_shows_all(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    _invoke(cli_runner, tmp_config_dir, "device", "add", "dev-a", _FP_A)
    _invoke(cli_runner, tmp_config_dir, "device", "add", "dev-b", _FP_B)
    result = _invoke(cli_runner, tmp_config_dir, "device", "list")
    assert result.exit_code == 0
    assert "dev-a" in result.output
    assert "dev-b" in result.output


def test_device_list_empty_shows_message(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    result = _invoke(cli_runner, tmp_config_dir, "device", "list")
    assert result.exit_code == 0
    assert result.output  # some message is shown


def test_device_mod_updates_fingerprint(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    _invoke(cli_runner, tmp_config_dir, "device", "add", "dev-a", _FP_A)
    result = _invoke(cli_runner, tmp_config_dir, "device", "mod", "dev-a", _FP_C)
    assert result.exit_code == 0
    devices = DevicesConfig.load(tmp_config_dir)
    dev = next(d for d in devices.trusted_devices if d.id == "dev-a")
    assert dev.fingerprint == _FP_C


def test_device_mod_nonexistent_fails(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    result = _invoke(cli_runner, tmp_config_dir, "device", "mod", "ghost", _FP_A)
    assert result.exit_code != 0


def test_device_mod_invalid_fingerprint_fails(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    _invoke(cli_runner, tmp_config_dir, "device", "add", "dev-a", _FP_A)
    result = _invoke(cli_runner, tmp_config_dir, "device", "mod", "dev-a", "bad-fp")
    assert result.exit_code != 0
    devices = DevicesConfig.load(tmp_config_dir)
    dev = next(d for d in devices.trusted_devices if d.id == "dev-a")
    assert dev.fingerprint == _FP_A


def test_device_mod_duplicate_fingerprint_fails(
    cli_runner: CliRunner, tmp_config_dir: Path
) -> None:
    _invoke(cli_runner, tmp_config_dir, "device", "add", "dev-a", _FP_A)
    _invoke(cli_runner, tmp_config_dir, "device", "add", "dev-b", _FP_B)
    result = _invoke(cli_runner, tmp_config_dir, "device", "mod", "dev-a", _FP_B)
    assert result.exit_code != 0
    devices = DevicesConfig.load(tmp_config_dir)
    dev_a = next(d for d in devices.trusted_devices if d.id == "dev-a")
    assert dev_a.fingerprint == _FP_A
