"""P2P node: TLS handshake, mutual auth and sync orchestration."""

import asyncio
import socket
import ssl
import time

import yaml

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

    async def start(self, host:str, port: int) -> None:
        """Starts the node as an async server or connects as an async client.
        
        This handles the initial network connection and automatically wraps it in TLS
        using the provided certificates.
        """

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
                reader, writer = await asyncio.open_connection(
                    host, port, ssl=context
                )
                await self.handle_secure_connection(reader, writer)
            except Exception as e:
                print(f"[!] Connection error: {e}")

    async def handle_secure_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Authenticate the peer and run a hello handshake over the secure streams.
        
        The partner is rejected if they do not present a certificate or if their certificate
        fingerprint is not in the `trusted_devices` list. After successful check, a brief "Hello" 
        handshake is performed.
        """
        context = create_tls_context(self.is_server, self.cert_path, self.key_path)

        try:
            # Mutual TLS Check: Who is on the other end?
            ssl_object = writer.get_extra_info('ssl_object')
            peer_cert: bytes | None = ssl_object.getpeercert(binary_form=True) if ssl_object else None

            if peer_cert:
                fingerprint: str = get_public_key_fingerprint(peer_cert)

                if fingerprint in self.trusted_devices:
                    print(f"[+] Verified: {self.trusted_devices[fingerprint]}")
                else:
                    auth_err = f"[-] Unknown device! Fingerprint: {fingerprint}"
                    raise PeerAuthError(auth_err)

            else:
                auth_err = "Peer did not present a certificate. Mutual TLS authentication required."
                raise PeerAuthError(auth_err)

            # Handshake
            if self.is_server:
                writer.write(b"Hello from server. Data sync can start.")
                await writer.drain()
                answer = await reader.read(1024)
                print(f"[*] Message from client: {answer.decode('utf-8')}")
            else:
                msg = await reader.read(1024)
                print(f"[*] Message from server: {msg.decode('utf-8')}")
                writer.write(b"Hello from client. I'm ready.")
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
