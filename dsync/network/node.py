"""P2P node: TLS handshake, mutual auth and sync orchestration."""

import asyncio
import ssl

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from dsync.state import AppState

from .p2p_core import create_tls_context, get_public_key_fingerprint, async_recv_msg, async_send_msg


class PeerAuthError(Exception):
    """Raised when mutual TLS peer authentication fails."""


class P2PNode:
    """Represents an endpoint (node) in the P2P network.

    Encapsulates the logic for establishing a secure connection via TLS,
    authenticating the remote party using certificate fingerprints,
    and handling the actual data synchronization process.
    """

    def __init__(
        self,
        is_server: bool,
        cert_path: str,
        key_path: str,
        state: AppState,
        trusted_cert_paths: list[str] | None = None,
    ) -> None:
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

    async def start(self, host: str, port: int) -> None:
        """Starts the node as an async server or connects as an async client.

        This handles the initial network connection and automatically wraps it in TLS
        using the provided certificates.
        """

        # Show error messages
        loop = asyncio.get_running_loop()

        def custom_exception_handler(loop, context):
            exc = context.get("exception")
            if isinstance(exc, ssl.SSLError):
                print(f"\n[!] TLS Handshake failed: {exc.reason} ({exc})")
            else:
                print(f"\n[!] Background error: {context.get('message')}")

        loop.set_exception_handler(custom_exception_handler)

        context = create_tls_context(self.is_server, self.cert_path, self.key_path)

        if self.is_server:
            server = await asyncio.start_server(
                self.handle_secure_connection, host, port, ssl=context
            )
            print(f"[*] Server listens asynchroniously on {host}:{port}")
            async with server:
                await server.serve_forever()
        else:
            try:
                reader, writer = await asyncio.open_connection(host, port, ssl=context)
                await self.handle_secure_connection(reader, writer)
            except Exception as e:
                print(f"[!] Connection error: {e}")

    async def handle_secure_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Authenticate the peer and run a hello handshake over the secure streams.

        The partner is rejected if they do not present a certificate or if their certificate
        fingerprint is not in the `trusted_devices` list. After successful check, a brief "Hello"
        handshake is performed.
        """
        try:
            # Mutual TLS Check: Who is on the other end?
            # ssl_object = writer.get_extra_info('ssl_object')
            # peer_cert: bytes | None = ssl_object.getpeercert(binary_form=True) if ssl_object else None

            # Send own certificate to peer
            with open(self.cert_path, "rb") as f:
                own_cert_pem = f.read()
            await async_send_msg(writer, 0, own_cert_pem)

            # Receive peer's certificate
            _, peer_cert_pem = await async_recv_msg(reader)
            if peer_cert_pem is None:
                raise PeerAuthError("Peer sent no certification")

            # PEM -> DER for fingerprint calculation
            cert = x509.load_pem_x509_certificate(peer_cert_pem)
            peer_cert_der = cert.public_bytes(Encoding.DER)

            fingerprint = get_public_key_fingerprint(peer_cert_der)
            if fingerprint not in self.trusted_devices:
                raise PeerAuthError(f"[-] Unknown device! Fingerprint: {fingerprint}")

            print(f"[+] Verified: {self.trusted_devices[fingerprint]}")

            # Handshake
            if self.is_server:
                print("[DEBUG] Server sending hello...")
                await async_send_msg(writer, 1, b"Hello from server. Data sync can start.")
                await writer.drain()
                print("[DEBUG] Server waiting for client reply...")
                _, answer = await async_recv_msg(reader)
                if answer is None:
                    raise PeerAuthError("Client closed connection before handshake reply.")
                print(f"[*] Message from client: {answer.decode('utf-8')}")
            else:
                print("[DEBUG] Client waiting for hello...")
                _, msg = await async_recv_msg(reader)
                if msg is None:
                    raise PeerAuthError("Server closed connection before handshake greeting.")
                print(f"[*] Message from server: {msg.decode('utf-8')}")
                print("[DEBUG] Client sending reply...")
                await async_send_msg(writer, 1, b"Hello from client. I'm ready.")
                await writer.drain()

            await self.start_sync(reader, writer)

        except Exception as e:
            print(f"[!] Connection error: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def start_sync(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Starts the data synchronization process.

        Placeholder.
        """
        pass
