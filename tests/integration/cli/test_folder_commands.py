from pathlib import Path

from typer.testing import CliRunner

from dsync.cli import cli
from dsync.config import FoldersConfig, SyncMode

_FP_A = "hex-" + "a" * 64
_FP_B = "hex-" + "b" * 64


def _invoke(runner: CliRunner, config_dir: Path, *args: str):
    return runner.invoke(cli, ["--config-dir", str(config_dir), *args])


def _add_device(runner: CliRunner, config_dir: Path, dev_id: str, fp: str) -> None:
    _invoke(runner, config_dir, "device", "add", dev_id, fp)


def test_folder_add_success(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    result = _invoke(
        cli_runner, tmp_config_dir, "folder", "add", "docs", "/data/docs", "--mode", "mirror"
    )
    assert result.exit_code == 0
    folders = FoldersConfig.load(tmp_config_dir)
    assert any(e.id == "docs" for e in folders.entries)


def test_folder_add_persisted_to_yaml(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    _invoke(cli_runner, tmp_config_dir, "folder", "add", "docs", "/data/docs", "--mode", "mirror")
    yaml_text = (tmp_config_dir / FoldersConfig.FILENAME).read_text()
    assert "docs" in yaml_text


def test_folder_add_with_device(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    _add_device(cli_runner, tmp_config_dir, "dev-a", _FP_A)
    result = _invoke(
        cli_runner,
        tmp_config_dir,
        "folder",
        "add",
        "photos",
        "/data/photos",
        "--mode",
        "backup-to-peer",
        "--device",
        "dev-a",
    )
    assert result.exit_code == 0
    folders = FoldersConfig.load(tmp_config_dir)
    entry = next(e for e in folders.entries if e.id == "photos")
    assert entry.devices == ["dev-a"]
    assert entry.mode == SyncMode.BACKUP_TO_PEER


def test_folder_add_unknown_device_fails(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    result = _invoke(
        cli_runner,
        tmp_config_dir,
        "folder",
        "add",
        "docs",
        "/data/docs",
        "--mode",
        "mirror",
        "--device",
        "nonexistent-device",
    )
    assert result.exit_code != 0
    folders = FoldersConfig.load(tmp_config_dir)
    assert all(e.id != "docs" for e in folders.entries)


def test_folder_add_duplicate_id_fails(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    _invoke(cli_runner, tmp_config_dir, "folder", "add", "docs", "/data/a", "--mode", "mirror")
    result = _invoke(
        cli_runner, tmp_config_dir, "folder", "add", "docs", "/data/b", "--mode", "mirror"
    )
    assert result.exit_code != 0
    folders = FoldersConfig.load(tmp_config_dir)
    assert sum(1 for e in folders.entries if e.id == "docs") == 1


def test_folder_rm_removes_entry(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    _invoke(cli_runner, tmp_config_dir, "folder", "add", "docs", "/data/docs", "--mode", "mirror")
    result = _invoke(cli_runner, tmp_config_dir, "folder", "rm", "docs")
    assert result.exit_code == 0
    folders = FoldersConfig.load(tmp_config_dir)
    assert all(e.id != "docs" for e in folders.entries)


def test_folder_rm_nonexistent_shows_error(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    result = _invoke(cli_runner, tmp_config_dir, "folder", "rm", "ghost")
    assert result.exit_code == 0
    assert result.output


def test_folder_list_shows_all_entries(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    _invoke(cli_runner, tmp_config_dir, "folder", "add", "docs", "/data/docs", "--mode", "mirror")
    _invoke(
        cli_runner,
        tmp_config_dir,
        "folder",
        "add",
        "pics",
        "/data/pics",
        "--mode",
        "backup-to-peer",
    )
    result = _invoke(cli_runner, tmp_config_dir, "folder", "list")
    assert result.exit_code == 0
    assert "docs" in result.output
    assert "pics" in result.output


def test_folder_list_empty_shows_message(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    result = _invoke(cli_runner, tmp_config_dir, "folder", "list")
    assert result.exit_code == 0
    assert result.output


def test_config_dir_absent_warns_but_works(cli_runner: CliRunner, tmp_path: Path) -> None:
    nonexistent = tmp_path / "no-such-dir"
    result = _invoke(cli_runner, nonexistent, "device", "list")
    assert result.exit_code == 0


def test_config_dir_is_file_raises(cli_runner: CliRunner, tmp_path: Path) -> None:
    file_not_dir = tmp_path / "notadir"
    file_not_dir.write_text("I am a file")
    result = _invoke(cli_runner, file_not_dir, "device", "list")
    assert result.exit_code != 0


def test_config_dir_missing_yaml_warns_and_continues(cli_runner: CliRunner, tmp_path: Path) -> None:
    config_dir = tmp_path / "conf"
    config_dir.mkdir()
    (config_dir / "devices.yaml").write_text("trusted_devices: []\n")
    result = _invoke(cli_runner, config_dir, "device", "list")
    assert result.exit_code == 0


def test_folder_device_not_in_trusted_raises_on_load(cli_runner: CliRunner, tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "devices.yaml").write_text("trusted_devices: []\n")
    (tmp_path / "folders.yaml").write_text(
        "entries:\n  - id: f1\n    path: /data\n    mode: mirror\n    devices: [ghost-device]\n"
    )
    result = _invoke(cli_runner, tmp_path, "folder", "list")
    assert result.exit_code != 0


def test_folder_mod_updates_path(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    _invoke(cli_runner, tmp_config_dir, "folder", "add", "docs", "/data/old", "--mode", "mirror")
    result = _invoke(cli_runner, tmp_config_dir, "folder", "mod", "docs", "--path", "/data/new")
    assert result.exit_code == 0
    folders = FoldersConfig.load(tmp_config_dir)
    entry = next(e for e in folders.entries if e.id == "docs")
    assert entry.path == Path("/data/new")


def test_folder_mod_updates_mode(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    _invoke(cli_runner, tmp_config_dir, "folder", "add", "docs", "/data", "--mode", "mirror")
    result = _invoke(
        cli_runner, tmp_config_dir, "folder", "mod", "docs", "--mode", "backup-to-peer"
    )
    assert result.exit_code == 0
    folders = FoldersConfig.load(tmp_config_dir)
    assert next(e for e in folders.entries if e.id == "docs").mode == SyncMode.BACKUP_TO_PEER


def test_folder_mod_updates_device(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    _add_device(cli_runner, tmp_config_dir, "dev-a", _FP_A)
    _add_device(cli_runner, tmp_config_dir, "dev-b", _FP_B)
    _invoke(
        cli_runner,
        tmp_config_dir,
        "folder",
        "add",
        "docs",
        "/data",
        "--mode",
        "mirror",
        "--device",
        "dev-a",
    )
    result = _invoke(cli_runner, tmp_config_dir, "folder", "mod", "docs", "--device", "dev-b")
    assert result.exit_code == 0
    folders = FoldersConfig.load(tmp_config_dir)
    assert next(e for e in folders.entries if e.id == "docs").devices == ["dev-b"]


def test_folder_mod_no_flags_fails(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    _invoke(cli_runner, tmp_config_dir, "folder", "add", "docs", "/data", "--mode", "mirror")
    result = _invoke(cli_runner, tmp_config_dir, "folder", "mod", "docs")
    assert result.exit_code != 0


def test_folder_mod_nonexistent_fails(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    result = _invoke(cli_runner, tmp_config_dir, "folder", "mod", "ghost", "--path", "/new")
    assert result.exit_code != 0


def test_folder_mod_unknown_device_fails(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    _invoke(cli_runner, tmp_config_dir, "folder", "add", "docs", "/data", "--mode", "mirror")
    result = _invoke(cli_runner, tmp_config_dir, "folder", "mod", "docs", "--device", "nobody")
    assert result.exit_code != 0
    folders = FoldersConfig.load(tmp_config_dir)
    entry = next(e for e in folders.entries if e.id == "docs")
    assert entry.devices is None
