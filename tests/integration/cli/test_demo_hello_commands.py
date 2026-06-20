from pathlib import Path

from typer.testing import CliRunner

from dsync.cli import cli


def _invoke(runner: CliRunner, config_dir: Path, *args: str):
    return runner.invoke(cli, ["--config-dir", str(config_dir), *args])


def test_hello_default_greeting(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    result = _invoke(cli_runner, tmp_config_dir, "hello")
    assert result.exit_code == 0
    assert "Hello, world!" in result.output


def test_hello_with_name(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    result = _invoke(cli_runner, tmp_config_dir, "hello", "Ada")
    assert result.exit_code == 0
    assert "Hello, Ada!" in result.output


def test_demo_add_item(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    result = _invoke(cli_runner, tmp_config_dir, "demo", "add", "item-1")
    assert result.exit_code == 0
    assert "item-1" in result.output


def test_demo_list_items(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    result = _invoke(cli_runner, tmp_config_dir, "demo", "list")
    assert result.exit_code == 0
