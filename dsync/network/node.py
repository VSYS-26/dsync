import yaml
import socket
import time
import ssl

from typing import Dict, Any
from .p2p_core import create_tls_context, get_public_key_fingerprint, send_msg, recv_msg

MSG_SYNC_HASHES = 1
MSG_REQUEST_CHUNKS = 2
MSG_CHUNK_DATA = 3

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
        trusted_devices: Dict[str, str]
    ) -> None:
        '''
        Initializes a new P2P node.

        Args:
            is_server (bool): Specifies whether this node acts as a server (waits for connections)
                            or as a client (establishes connections).
            cert_path (str): The file path to ones own TLS certificate (.pem).
            key_path (str): The file path to ones own private key (.pem).
            trusted_devices (Dict[str, str]): A dictionary that maps certificate fingerprints (keys)
                                            to device names (values). Serves as a whitelist.
        '''
        self.is_server = is_server
        self.cert_path = cert_path
        self.key_path = key_path
        self.trusted_devices = trusted_devices

    def handle_secure_connection(self, raw_socket: socket.socket) -> bool:
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
            tls_socket = context.wrap_socket(raw_socket, server_side=self.is_server)
            # Mutual TLS Check: Who is on the other end?
            peer_cert: Dict[str, Any] | None = tls_socket.getpeercert(binary_form=True)

            if peer_cert:
                fingerprint: str = get_public_key_fingerprint(peer_cert)

                if fingerprint in self.trusted_devices:
                    print(f"[+] Verified: {self.trusted_devices[fingerprint]}")
                else:
                    raise Exception(f"[-] Unknown device! Fingerprint: {fingerprint}")
            
            else:
                if self.is_server:
                    print("[*] Info: Client connected.")
                else:
                    raise Exception("The server did not present a certificate!")
            
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

    def start_sync(self, tls_socket: ssl.SSLSocket) -> None:
        '''
        Starts the data synchronization process over an already established, secure TLS connection.

        The client first sends a list of its existing file hashes.
        The server receives this list, compares it with its own, and then specifically
        request the data blocks (chunks) that are still missing.

        Args:
            tls_socket (ssl.SSLSocket): The encrypted socket trough which messages are exchanged.
        '''
        if self.is_server:
            print("[*] Wait on hash list from peer...")
            msg_type, data = recv_msg(tls_socket)
            if msg_type == MSG_SYNC_HASHES and data is not None:
                remote_hashes: Dict[str, str] = yaml.safe_load(data.decode('utf-8'))
                print(f"[*] Received hashes: {remote_hashes}")

                missing = ["chunk_2", "chunk_3"]

                print("[*] Request missing chunks...")
                request_data: bytes = yaml.dump(missing).encode('utf-8')
                send_msg(tls_socket, MSG_REQUEST_CHUNKS, request_data)

        else:
            my_hashes: Dict[str, str] = {"chunk_1": "abc...", "chunk_2": "def...", "chunk_3": "ghi..."}
            print("[*] Send file hashes...")
            send_msg(tls_socket, MSG_SYNC_HASHES, yaml.dump(my_hashes).encode('utf-8'))

            msg_type, data = recv_msg(tls_socket)
            if msg_type == MSG_REQUEST_CHUNKS and data is not None:
                requested_chunks = yaml.safe_load(data.decode('utf-8'))
                print(f"[*] Peer requests: {requested_chunks}")
            
        tls_socket.close()
        print("[+] Sync finished.")
