"""P2P node: TLS handshake, mutual auth and sync orchestration."""

import asyncio
import hashlib
from pathlib import Path
import ssl
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_der_public_key,
    load_pem_private_key,
)

from dsync.network.errors import PeerAuthError
from dsync.network.file_transfer import recv_file, send_file
from dsync.network.config_exchange import ConfigExchange
from dsync.state import AppState
from dsync.config import FolderEntry, FoldersConfig, SyncMode

from .p2p_core import (
    MsgType,
    async_recv_auth_msg,
    async_recv_msg,
    async_send_msg,
    create_tls_context,
    get_tls_channel_binding,
)

_SPKI_SIZE = 294  # RSA-2048 SubjectPublicKeyInfo DER
_SIG_SIZE = 256  # RSA-2048 PSS signature
RECEIVED_DIR = Path(__file__).resolve().parents[2] / "received-files"


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
        folder: FolderEntry | None = None,
    ) -> None:
        """Initializes a new P2P node.

        Args:
            is_server (bool): Specifies whether this node acts as a server (waits for connections)
                            or as a client (establishes connections).
            cert_path (str): The file path to ones own TLS certificate (.pem).
            key_path (str): The file path to ones own private key (.pem).
            state (AppState): The global application runtime state containing configurations.
            folder (FolderEntry): Optional folder configuration for sync (client only).
        """
        self.is_server = is_server
        self.cert_path = cert_path
        self.key_path = key_path
        self.state = state
        self.folder = folder

        self.trusted_devices: dict[str, str] = {
            device.fingerprint: device.id for device in self.state.devices.trusted_devices
        }

        with Path(self.key_path).open("rb") as f:
            raw_key = load_pem_private_key(f.read(), password=None)
        if not isinstance(raw_key, RSAPrivateKey):
            raise TypeError("Only RSA private keys are supported for peer auth")
        if raw_key.key_size != 2048:
            raise TypeError(f"Only RSA-2048 keys are supported, got RSA-{raw_key.key_size}")
        self._own_private_key: RSAPrivateKey = raw_key
        self._own_spki: bytes = raw_key.public_key().public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo
        )

    async def start(self, host: str, port: int) -> None:
        """Starts the node as an async server or connects as an async client.

        This handles the initial network connection and automatically wraps it in TLS
        using the provided certificates.
        """
        # Show error messages
        loop = asyncio.get_running_loop()

        def custom_exception_handler(
            _loop: asyncio.AbstractEventLoop, context: dict[str, Any]
        ) -> None:
            """Print TLS handshake and background errors raised inside the event loop."""
            exc = context.get("exception")
            if isinstance(exc, ssl.SSLError):
                print(f"[!] TLS Handshake failed: {exc.reason} ({exc})")
            else:
                print(f"[!] Background error: {context.get('message')}")

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

    def _sign_channel_binding(self, channel_binding: bytes) -> bytes:
        """Sign the TLS channel binding with own RSA private key (PSS/SHA-256)."""
        return self._own_private_key.sign(
            channel_binding,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )

    @staticmethod
    def _verify_peer_signature(
        public_key: RSAPublicKey, channel_binding: bytes, signature: bytes
    ) -> None:
        """Raise ValueError if signature does not verify against channel_binding."""
        try:
            public_key.verify(
                signature,
                channel_binding,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
        except Exception as exc:
            raise ValueError(f"Peer signature invalid: {exc}") from exc

    @staticmethod
    def _pack_auth_msg(spki: bytes, signature: bytes) -> bytes:
        """Pack SPKI + signature into a fixed-size payload: [294B spki][256B sig]."""
        return spki + signature

    @staticmethod
    def _unpack_auth_msg(payload: bytes) -> tuple[bytes, bytes]:
        """Unpack fixed-size payload into (spki, signature)."""
        return payload[:_SPKI_SIZE], payload[_SPKI_SIZE:]

    async def handle_secure_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Authenticate the peer and run a hello handshake over the secure streams.

        Authentication is MITM-resistant via TLS channel binding:
          1. Derive 32-byte channel binding from the live TLS session
             (export_keying_material — unique per connection, bound to TLS master secret).
          2. Sign the channel binding with own RSA private key.
          3. Send own cert + signature atomically; receive peer's cert + signature.
          4. Verify peer fingerprint against trusted list.
          5. Verify peer's signature over *this* channel binding using peer's public key.

        An active MITM has different channel bindings for each of its two tunnels.
        It cannot forge a valid signature over the victim-side channel binding without
        the victim's private key, so step 5 fails.
        """
        try:
            channel_binding = get_tls_channel_binding(writer)
            own_sig = self._sign_channel_binding(channel_binding)
            auth_payload = self._pack_auth_msg(self._own_spki, own_sig)

            await async_send_msg(writer, MsgType.AUTH, auth_payload)

            peer_payload = await async_recv_auth_msg(reader)

            peer_spki, peer_sig = self._unpack_auth_msg(peer_payload)

            fingerprint = hashlib.sha256(peer_spki).hexdigest()
            if fingerprint not in self.trusted_devices:
                raise PeerAuthError(f"[-] Unknown device! Fingerprint: {fingerprint}")

            peer_public_key = load_der_public_key(peer_spki)
            if not isinstance(peer_public_key, RSAPublicKey):
                raise PeerAuthError("Peer cert must use RSA key")
            self._verify_peer_signature(peer_public_key, channel_binding, peer_sig)

            peer_id = self.trusted_devices[fingerprint]
            if "/" in peer_id or "\\" in peer_id or peer_id in {".", ".."}:
                raise PeerAuthError(f"Refusing path-unsafe peer id: {peer_id!r}")
            print(f"[+] Verified: {peer_id}")

            # Handshake
            if self.is_server:
                await async_send_msg(
                    writer, MsgType.HELLO, b"Hello from server. Data sync can start."
                )
                msg_type, answer = await async_recv_msg(reader)
                if answer is None:
                    raise PeerAuthError("Client closed connection before handshake reply.")
                if msg_type != MsgType.HELLO:
                    raise PeerAuthError(
                        f"Expected hello (type {MsgType.HELLO}), got type {msg_type}"
                    )
                print(f"[*] Message from client: {answer.decode('utf-8')}")
            else:
                msg_type, msg = await async_recv_msg(reader)
                if msg is None:
                    raise PeerAuthError("Server closed connection before handshake greeting.")
                if msg_type != MsgType.HELLO:
                    raise PeerAuthError(
                        f"Expected hello (type {MsgType.HELLO}), got type {msg_type}"
                    )
                print(f"[*] Message from server: {msg.decode('utf-8')}")
                await async_send_msg(writer, MsgType.HELLO, b"Hello from client. I'm ready.")

            await self.start_sync(reader, writer, peer_id)

        except Exception as e:
            print(f"[!] Connection error: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def start_sync(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        peer_id: str,
    ) -> None:
        """Transfer the configured test files over the authenticated stream.

        Server side receives files into ``RECEIVED_DIR/<peer_id>/<filename>`` until the
        sender closes the stream. Client side sends each file in
        ``TEST_FILES`` from ``TEST_FILES_DIR`` and lets the caller close
        the writer to signal end-of-transfer (TLS streams do not support
        ``write_eof``).

        Args:
            reader: Authenticated asyncio stream reader from the connection
                setup.
            writer: Authenticated asyncio stream writer from the connection
                setup.
            peer_id: Verified trusted-device id of the remote peer. Used as
                the per-client subdirectory name on the server side.
        """
        exchange = ConfigExchange()

        if self.is_server:
            # Server receives config first, validates modes
            received_config = await exchange.exchange_as_peer(reader, writer)

            # Validate each folder config against local config
            for remote_entry in received_config.entries:
                local_entry = next((e for e in self.state.folders.entries if e.id == remote_entry.id), None)
                if not local_entry:
                    raise PeerAuthError(f"Peer wants to sync '{remote_entry.id}' but not configured locally")

                # Mode compatibility check
                if remote_entry.mode == SyncMode.MIRROR:
                    if local_entry.mode != SyncMode.MIRROR:
                        raise PeerAuthError(f"Mode mismatch for '{remote_entry.id}': peer has mirror, local has {local_entry.mode.value}")
                elif remote_entry.mode == SyncMode.BACKUP_TO_PEER:
                    if local_entry.mode != SyncMode.BACKUP_FROM_PEER:
                        raise PeerAuthError(f"Mode mismatch for '{remote_entry.id}': peer backup-to-peer requires local backup-from-peer, but local is {local_entry.mode.value}")
                elif remote_entry.mode == SyncMode.BACKUP_FROM_PEER:
                    raise PeerAuthError(f"Peer attempted to send in backup-from-peer mode for '{remote_entry.id}' - invalid direction")

            print(f"[+] Config validated for {len(received_config.entries)} folder(s)")

            peer_dir = RECEIVED_DIR / peer_id
            peer_dir.mkdir(parents=True, exist_ok=True)
            while True:
                try:
                    await recv_file(reader, writer, peer_dir)
                except asyncio.IncompleteReadError as e:
                    if e.partial:
                        raise
                    break
        else:
            # Client sends config first
            if not self.folder:
                raise ValueError("Client mode requires folder to be set")

            config_to_send = FoldersConfig(entries=[self.folder])
            await exchange.exchange_as_source(writer, reader, config_to_send)

            folder_path = Path(self.folder.path)

            if not folder_path.exists():
                raise FileNotFoundError(f"Folder {folder_path} does not exist")

            if not folder_path.is_dir():
                raise NotADirectoryError(f"{folder_path} is not a directory")

            files_to_send = list(folder_path.rglob("*")) if self.folder.recursive else list(folder_path.glob("*"))
            files_to_send = [f for f in files_to_send if f.is_file()]

            if not files_to_send:
                print(f"[DEBUG] No files to send in {folder_path}")
                return

            print(f"[DEBUG] Sending {len(files_to_send)} file(s) from {folder_path}")
            for file_path in files_to_send:
                await send_file(writer, reader, file_path)