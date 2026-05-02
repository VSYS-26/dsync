import asyncio
import socket
import ssl
import time
import yaml


from typing import Dict, Any

from dsync.state import AppState

from .p2p_core import create_tls_context, get_public_key_fingerprint, send_msg, recv_msg

MSG_SYNC_HASHES = 1
MSG_REQUEST_CHUNKS = 2

class P2PNode:
    '''
    Represents an endpoint (node) in the P2P network.

    Encapsulates the logic for establishing a secure connection via TLS,
    authenticating the remote party using certificate fingerprints,
    and handling the actual data synchronization process.
    '''
    def __init__(
        self,
        is_server: bool,
        cert_path: str,
        key_path: str,
        state: AppState
    ) -> None:
        '''
        Initializes a new P2P node.

        Args:
            is_server (bool): Specifies whether this node acts as a server (waits for connections)
                            or as a client (establishes connections).
            cert_path (str): The file path to ones own TLS certificate (.pem).
            key_path (str): The file path to ones own private key (.pem).
            state (AppState): The global application runtime state containing configurations.
        '''
        self.is_server = is_server
        self.cert_path = cert_path
        self.key_path = key_path
        self.state = state

        self.trusted_devices: Dict[str, str] = {
            device.fingerprint: device.id
            for device in self.state.devices.trusted_devices
        } 

    async def handle_secure_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        '''
        Takes an unencrypted socket connection, converts it into a secure TLS connection,
        and authenticates the communication partner (Mutual TLS).

        The partner is rejected if they do not present a certificate or if their certificate
        fingerprint is not in the `trusted_devices` list. After successful check, a brief "Hello"
        handshake is performed.

        Args:
            raw_socket (socket.socket): The initial, unencrypted network connection.
        '''
        context = create_tls_context(self.is_server, self.cert_path, self.key_path)

        try:
            # TLS Wrap
            ssl_object = writer.get_extra_info('ssl_object')
            peer_cert = ssl_object.getpeercert(binary_form=True) if ssl_object else None

            if peer_cert:
                fingerprint: str = get_public_key_fingerprint(peer_cert)

                if fingerprint in self.trusted_devices:
                    print(f"[+] Verified: {self.trusted_devices[fingerprint]}")
                else:
                    raise Exception(f"[-] Unknown device! Fingerprint: {fingerprint}")
            
            else:
                raise Exception("Peer did not present a certificate. Mutual TLS authentication required.")
            
            if self.is_server:
                # Server sends first, then waits on answer
                writer.write(b"Hello from server. Data sync can start.")
                answer = await reader.read(1024)
                print(f"[*] Message from client: {answer.decode('utf-8')}")
            else:
                # Client waits on message, then sends answer
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

    async def start_sync(self, host: str, port: int):
        '''
        Starts the data synchronization process over an already established, secure TLS connection.

        The client first sends a list of its existing file hashes.
        The server receives this list, compares it with its own, and then specifically
        request the data blocks (chunks) that are still missing.

        Args:
            tls_socket (ssl.SSLSocket): The encrypted socket trough which messages are exchanged.
        '''
        context = create_tls_context(self.is_server, self.cert_path, self.key_path)

        if self.is_server:
            server = await asyncio.start_server(
                self.handle_secure_connection, host, port, ssl=context
            )
            print(f"[+] Server runs asynchroniously on {host}:{port}")
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
