"""CLI command to show the peer map stored locally."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from dsync.cli.console import info, warn
from dsync.identity import PeerMapStore


def show_map(
    map_file: Annotated[
        Path,
        typer.Option(
            "--map-file",
            help="Path to peer map JSON file",
        ),
    ] = Path(".dsync") / "peer-map.json",
) -> None:
    """Show the current fingerprint-to-IPv4 mapping."""
    peers = PeerMapStore(file_path=map_file).list_peers()
    if not peers:
        warn("Peer map is empty")
        return

    for peer in peers.values():
        info(f"{peer.fingerprint} -> {peer.ipv4} (expires_at={peer.expires_at})")
