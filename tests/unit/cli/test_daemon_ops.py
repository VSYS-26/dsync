from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer

from dsync.cli.daemon_ops import refresh_server_daemon, run_disable, run_enable, run_status
from dsync.config import DaemonConfig, DevicesConfig, FoldersConfig
from dsync.state import AppState


def _state(tmp_path: Path, *, daemon_enabled: bool) -> AppState:
    return AppState(
        config_dir=tmp_path,
        folders=FoldersConfig(entries=[]),
        devices=DevicesConfig(trusted_devices=[]),
        daemon=DaemonConfig(enabled=daemon_enabled),
    )


# ── run_enable ──────────────────────────────────────────────────────────────


def test_run_enable_already_enabled_skips_enable() -> None:
    daemon = MagicMock()
    daemon.is_enabled.return_value = True
    save_config = MagicMock()

    run_enable(daemon, label="server", save_config=save_config)

    daemon.enable.assert_not_called()
    save_config.assert_not_called()


def test_run_enable_calls_enable_and_saves_config() -> None:
    daemon = MagicMock()
    daemon.is_enabled.return_value = False
    save_config = MagicMock()

    run_enable(daemon, label="server", save_config=save_config)

    daemon.enable.assert_called_once()
    save_config.assert_called_once()


def test_run_enable_not_implemented_exits_nonzero() -> None:
    daemon = MagicMock()
    daemon.is_enabled.return_value = False
    daemon.enable.side_effect = NotImplementedError("unsupported platform")

    with pytest.raises(typer.Exit):
        run_enable(daemon, label="server", save_config=MagicMock())


def test_run_enable_permission_error_exits_nonzero() -> None:
    daemon = MagicMock()
    daemon.is_enabled.return_value = False
    daemon.enable.side_effect = PermissionError

    with pytest.raises(typer.Exit):
        run_enable(daemon, label="server", save_config=MagicMock())


def test_run_enable_generic_exception_exits_nonzero() -> None:
    daemon = MagicMock()
    daemon.is_enabled.return_value = False
    daemon.enable.side_effect = RuntimeError("boom")

    with pytest.raises(typer.Exit):
        run_enable(daemon, label="server", save_config=MagicMock())


# ── run_disable ─────────────────────────────────────────────────────────────


def test_run_disable_not_enabled_skips_disable() -> None:
    daemon = MagicMock()
    daemon.is_enabled.return_value = False
    save_config = MagicMock()

    run_disable(daemon, label="server", save_config=save_config)

    daemon.disable.assert_not_called()
    save_config.assert_not_called()


def test_run_disable_calls_disable_and_saves_config() -> None:
    daemon = MagicMock()
    daemon.is_enabled.return_value = True
    save_config = MagicMock()

    run_disable(daemon, label="server", save_config=save_config)

    daemon.disable.assert_called_once()
    save_config.assert_called_once()


def test_run_disable_not_implemented_exits_nonzero() -> None:
    daemon = MagicMock()
    daemon.is_enabled.return_value = True
    daemon.disable.side_effect = NotImplementedError("unsupported platform")

    with pytest.raises(typer.Exit):
        run_disable(daemon, label="server", save_config=MagicMock())


def test_run_disable_permission_error_exits_nonzero() -> None:
    daemon = MagicMock()
    daemon.is_enabled.return_value = True
    daemon.disable.side_effect = PermissionError

    with pytest.raises(typer.Exit):
        run_disable(daemon, label="server", save_config=MagicMock())


def test_run_disable_generic_exception_exits_nonzero() -> None:
    daemon = MagicMock()
    daemon.is_enabled.return_value = True
    daemon.disable.side_effect = RuntimeError("boom")

    with pytest.raises(typer.Exit):
        run_disable(daemon, label="server", save_config=MagicMock())


# ── run_status ──────────────────────────────────────────────────────────────


def test_run_status_enabled_and_running_calls_extra_info() -> None:
    daemon = MagicMock()
    daemon.is_enabled.return_value = True
    daemon.is_running.return_value = True
    extra_info = MagicMock()

    run_status(daemon, label="server", extra_info=extra_info)

    extra_info.assert_called_once()


def test_run_status_disabled_skips_is_running_and_extra_info() -> None:
    daemon = MagicMock()
    daemon.is_enabled.return_value = False
    extra_info = MagicMock()

    run_status(daemon, label="server", extra_info=extra_info)

    daemon.is_running.assert_not_called()
    extra_info.assert_not_called()


def test_run_status_without_extra_info_callback() -> None:
    daemon = MagicMock()
    daemon.is_enabled.return_value = True
    daemon.is_running.return_value = False

    run_status(daemon, label="server", extra_info=None)


def test_run_status_not_implemented_exits_nonzero() -> None:
    daemon = MagicMock()
    daemon.is_enabled.side_effect = NotImplementedError("unsupported platform")

    with pytest.raises(typer.Exit):
        run_status(daemon, label="server")


def test_run_status_generic_exception_exits_nonzero() -> None:
    daemon = MagicMock()
    daemon.is_enabled.side_effect = RuntimeError("boom")

    with pytest.raises(typer.Exit):
        run_status(daemon, label="server")


# ── refresh_server_daemon ───────────────────────────────────────────────────


def test_refresh_server_daemon_noop_when_not_enabled(tmp_path: Path) -> None:
    state = _state(tmp_path, daemon_enabled=False)

    with patch("dsync.daemon.daemons.ServerDaemon") as mock_cls:
        refresh_server_daemon(state)

    mock_cls.assert_not_called()


def test_refresh_server_daemon_noop_when_not_running(tmp_path: Path) -> None:
    state = _state(tmp_path, daemon_enabled=True)
    mock_daemon = MagicMock()
    mock_daemon.is_running.return_value = False

    with patch("dsync.daemon.daemons.ServerDaemon", return_value=mock_daemon):
        refresh_server_daemon(state)

    mock_daemon.restart.assert_not_called()


def test_refresh_server_daemon_restarts_when_running(tmp_path: Path) -> None:
    state = _state(tmp_path, daemon_enabled=True)
    mock_daemon = MagicMock()
    mock_daemon.is_running.return_value = True

    with patch("dsync.daemon.daemons.ServerDaemon", return_value=mock_daemon):
        refresh_server_daemon(state)

    mock_daemon.restart.assert_called_once()
