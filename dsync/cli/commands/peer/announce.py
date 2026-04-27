from __future__ import annotations

import time
from typing import Annotated

import typer

from dsync.cli.console import info, success, warn
from dsync.crypto import load_keypair, public_key_fingerprint
from dsync.network.discovery import FingerprintAnnouncer


def announce(
    seconds: Annotated[int, typer.Option("--seconds", "-s", help="Broadcast duration in seconds")] = 30,
) -> None:
    """Broadcast this device fingerprint over local multicast."""
    try:
        _, public_key_pem = load_keypair()
    except FileNotFoundError:
        warn("No local keypair found. Generate/import keys first.")
        raise typer.Exit(code=1) from None

    fingerprint = public_key_fingerprint(public_key_pem)
    announcer = FingerprintAnnouncer(fingerprint=fingerprint)

    info(f"Announcing fingerprint {fingerprint} for {seconds}s")
    announcer.start()
    try:
        time.sleep(max(seconds, 0))
    except KeyboardInterrupt:
        warn("Stopped by user")
    finally:
        announcer.stop()

    success("Announce finished")

