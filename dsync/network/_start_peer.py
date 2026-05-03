"""Two-peer TLS demo running on localhost.

Spawns one server peer and one client peer in separate threads, both bound to
``127.0.0.1:9999``. Each peer loads its own certificate/key pair and trusts the
other peer's public-key fingerprint via an in-memory ``AppState``.

Run as a module from the repo root::

    python -m dsync.network._start_peer

Prerequisites:
    - ``peer_a_cert.pem`` / ``peer_a_key.pem`` and ``peer_b_cert.pem`` /
      ``peer_b_key.pem`` exist in the current working directory.
    - The placeholder fingerprints below are replaced with the real SHA-256
      fingerprints of the corresponding certificates.
"""

from pathlib import Path
import socket
import threading
import time

from dsync.config import DevicesConfig, FoldersConfig, TrustedDevice
from dsync.network.node import P2PNode
from dsync.state import AppState

PEER_A_FINGERPRINT = "ADD_FINGERPRINT_FROM_PEER_A"
PEER_B_FINGERPRINT = "ADD_FINGERPRINT_FROM_PEER_B"

HOST = "127.0.0.1"
PORT = 9999


def _build_demo_state() -> AppState:
    """Build an in-memory ``AppState`` trusting both demo peers."""
    devices = DevicesConfig(
        trusted_devices=[
            TrustedDevice(id="Peer A (Desktop)", fingerprint=PEER_A_FINGERPRINT),
            TrustedDevice(id="Peer B (Laptop)", fingerprint=PEER_B_FINGERPRINT),
        ]
    )
    return AppState(config_dir=Path(), folders=FoldersConfig(), devices=devices)


def start_server_peer() -> None:
    """Start the server peer, listen on ``127.0.0.1:9999``, and accept one client."""
    node = P2PNode(
        is_server=True,
        cert_path="peer_a_cert.pem",
        key_path="peer_a_key.pem",
        state=_build_demo_state(),
    )

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(1)
    print("Peer A waits on connections...")

    raw_socket, _ = server_sock.accept()
    node.handle_secure_connection(raw_socket)


def start_client_peer() -> None:
    """Start the client peer and connect to the server on ``127.0.0.1:9999``."""
    time.sleep(1)
    node = P2PNode(
        is_server=False,
        cert_path="peer_b_cert.pem",
        key_path="peer_b_key.pem",
        state=_build_demo_state(),
    )

    raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_socket.connect((HOST, PORT))

    node.handle_secure_connection(raw_socket)


if __name__ == "__main__":
    t1 = threading.Thread(target=start_server_peer)
    t2 = threading.Thread(target=start_client_peer)

    t1.start()
    t2.start()

    t1.join()
    t2.join()
