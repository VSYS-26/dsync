"""CLI command to trigger a folder sync via the running relay-connect daemon.

The QUIC connection lives on the long-running ``dsync relay connect`` daemon
(so the relay-observed NAT mapping is preserved). ``run_backup`` is a
short-lived shell command that just asks the daemon over a Unix-domain
socket to perform the sync.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated

import typer

from dsync.cli.console import error, info, success, warn
from dsync.network.local_ipc import (
    LocalControlClient,
    SyncFolderRequest,
)

if TYPE_CHECKING:
    from dsync.state import AppState


def sync(
    ctx: typer.Context,
    folder_id: Annotated[
        str | None,
        typer.Option(
            "--folder-id",
            "-f",
            help="Specific folder ID (omit to sync all configured folders)",
        ),
    ] = None,
) -> None:
    """Trigger a folder sync via the running ``dsync relay connect`` daemon.

    Without ``--folder-id``: every folder in ``folders.yaml`` is synced with
    its configured peers. With ``--folder-id``: only that folder.
    """
    state: AppState = ctx.obj

    if not state.folders.entries:
        warn("No folders configured in folders.yaml")
        raise typer.Exit(code=1)
    if not state.devices.trusted_devices:
        warn("No trusted devices configured in devices.yaml")
        raise typer.Exit(code=1)

    try:
        client = LocalControlClient.discover()
    except FileNotFoundError as exc:
        error(
            f"{exc}\n"
            "Run `dsync relay connect <relay-id>` in another terminal first."
        )
        raise typer.Exit(code=1) from exc

    if folder_id is None:
        folders_to_sync = list(state.folders.entries)
        info(f"Syncing all {len(folders_to_sync)} configured folder(s) via daemon...")
    else:
        folder = next((f for f in state.folders.entries if f.id == folder_id), None)
        if folder is None:
            error(f"Folder '{folder_id}' not found in folders.yaml")
            raise typer.Exit(code=1)
        folders_to_sync = [folder]
        info(f"Syncing folder '{folder_id}' via daemon...")

    total = 0
    failed = 0
    for folder in folders_to_sync:
        peers = folder.devices or [d.id for d in state.devices.trusted_devices]
        info(f"\nFolder '{folder.id}' → {len(peers)} peer(s)")
        for peer_id in peers:
            try:
                response = asyncio.run(
                    client.request(
                        SyncFolderRequest(folder_id=folder.id, peer_id=peer_id),
                    )
                )
            except Exception as exc:
                error(f"  [{peer_id}] IPC error: {exc}")
                failed += 1
                continue
            if response.status == "ok":
                success(f"  [{peer_id}] synced")
                total += 1
            else:
                error(f"  [{peer_id}] {response.reason}")
                failed += 1

    info(f"\n{'=' * 60}")
    if failed > 0:
        warn(f"Completed: {total} successful, {failed} failed")
        raise typer.Exit(code=1)
    success(f"Completed: {total} successful sync(s)")
