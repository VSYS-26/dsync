from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from dsync.cli.console import info, success, warn
from dsync.crypto import load_keypair, public_key_fingerprint
from dsync.identity import PeerMapStore
from dsync.network.discovery import PeerDiscoveryRunner


def discover(
    seconds: Annotated[int, typer.Option("--seconds", "-s", help="Discovery duration in seconds")] = 10,
    map_file: Annotated[
        Path,
        typer.Option(
            "--map-file",
            help="Path to peer map JSON file",
        ),
    ] = Path(".dsync") / "peer-map.json",
) -> None:
    """Listen for peers and persist fingerprint-to-IPv4 mapping."""
    own_fingerprint: str | None = None
    try:
        _, public_key_pem = load_keypair()
        own_fingerprint = public_key_fingerprint(public_key_pem)
    except FileNotFoundError:
        warn("No local keypair found, own fingerprint filtering disabled.")

    store = PeerMapStore(file_path=map_file)
    runner = PeerDiscoveryRunner(store=store)

    info(f"Discovering peers for {seconds}s")
    peers, stats = runner.discover(duration_seconds=seconds, own_fingerprint=own_fingerprint)

    info(f"Events seen: {stats.events_seen}, mappings written: {stats.peers_written}")
    if not peers:
        warn("No peers found")
        return

    success(f"Active peers: {len(peers)}")
    for peer in peers.values():
        info(f"{peer.fingerprint} -> {peer.ipv4} (expires_at={peer.expires_at})")

