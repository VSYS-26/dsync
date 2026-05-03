"""P2P node: TLS handshake, mutual auth and sync orchestration."""

import asyncio
from pathlib import Path
import socket
import time

from dsync.network.transfer import recv_file, send_file
from dsync.state import AppState

from .p2p_core import create_tls_context, get_public_key_fingerprint

TEST_FILES = ("hello.txt", "sample.json", "icon.png")
TEST_FILES_DIR = Path(__file__).resolve().parents[2] / "test-files-to-send"
RECEIVED_DIR = Path(__file__).resolve().parents[2] / "received-files"


class PeerAuthError(Exception):
    """Raised when mutual TLS peer authentication fails."""


class P2PNode:
    """Represents an endpoint (node) in the P2P network.

    Encapsulates the logic for establishing a secure connection via TLS,
    authenticating the remote party using certificate fingerprints,
    and handling the actual data synchronization process.
    """

    def __init__(self, is_server: bool, cert_path: str, key_path: str, state: AppState) -> None:
        """Initializes a new P2P node.

        Args:
            is_server (bool): Specifies whether this node acts as a server (waits for connections)
                            or as a client (establishes connections).
            cert_path (str): The file path to ones own TLS certificate (.pem).
            key_path (str): The file path to ones own private key (.pem).
            state (AppState): The global application runtime state containing configurations.
        """
        self.is_server = is_server
        self.cert_path = cert_path
        self.key_path = key_path
        self.state = state

        self.trusted_devices: dict[str, str] = {
            device.fingerprint: device.id for device in self.state.devices.trusted_devices
        }

    def handle_secure_connection(self, raw_socket: socket.socket) -> bool:
        """Wrap ``raw_socket`` in TLS, authenticate the peer and run a hello handshake.

        The partner is rejected if they do not present a certificate or if their certificate
        fingerprint is not in the `trusted_devices` list. After successful check, a brief "Hello"
        handshake is performed.

        Args:
            raw_socket (socket.socket): The initial, unencrypted network connection.
        """
        context = create_tls_context(self.is_server, self.cert_path, self.key_path)

        try:
            # TLS Wrap
            tls_socket = context.wrap_socket(raw_socket, server_side=self.is_server)
            # Mutual TLS Check: Who is on the other end?
            peer_cert: bytes | None = tls_socket.getpeercert(binary_form=True)

            if peer_cert:
                fingerprint: str = get_public_key_fingerprint(peer_cert)

                if fingerprint in self.trusted_devices:
                    print(f"[+] Verified: {self.trusted_devices[fingerprint]}")
                else:
                    auth_err = f"[-] Unknown device! Fingerprint: {fingerprint}"
                    raise PeerAuthError(auth_err)  # noqa: TRY301

            else:
                auth_err = "Peer did not present a certificate. Mutual TLS authentication required."
                raise PeerAuthError(auth_err)  # noqa: TRY301

            if self.is_server:
                # Server sends first, then waits on answer
                tls_socket.sendall(b"Hello from server. Data sync can start.")
                answer = tls_socket.recv(1024)
                print(f"[*] Message from client: {answer.decode('utf-8')}")
            else:
                # Client waits on message, then sends answer
                msg = tls_socket.recv(1024)
                print(f"[*] Message from server: {msg.decode('utf-8')}")
                tls_socket.sendall(b"Hello from client. I'm ready.")

            time.sleep(1)
            self.start_sync(tls_socket)

        except Exception as e:
            print(f"[!] Connection error: {e}")
            raw_socket.close()
            return False
        return True

    async def start_sync(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Transfer the configured test files over the authenticated stream.

        Server side receives files until the sender closes the stream and
        writes them under ``RECEIVED_DIR``. Client side sends each file in
        ``TEST_FILES`` from ``TEST_FILES_DIR`` and signals end-of-transfer
        with ``writer.write_eof()``.

        Args:
            reader: Authenticated asyncio stream reader from the connection
                setup.
            writer: Authenticated asyncio stream writer from the connection
                setup.
        """
        if self.is_server:
            RECEIVED_DIR.mkdir(exist_ok=True)
            # TODO: future ticket — sender adds a folder/file id (from
            # AppState.folders) to the meta frame. Receiver resolves
            # id -> destination path via its own AppState.folders. Until
            # then, every file lands flat in RECEIVED_DIR.
            while True:
                try:
                    await recv_file(reader, RECEIVED_DIR)
                except asyncio.IncompleteReadError as e:
                    if e.partial:
                        raise
                    break
        else:
            for name in TEST_FILES:
                src = TEST_FILES_DIR / name
                await send_file(writer, src)
            writer.write_eof()
            await writer.drain()
