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
import yaml

from dsync.config import FolderEntry, FoldersConfig
from dsync.network.config_exchange import ConfigExchange
from dsync.network.config_validation import validate_peer_folder_config
from dsync.network.errors import ConfigConflictError, FrameValidationError, PeerAuthError
from dsync.network.file_transfer import (
    apply_renames,
    recv_folder_and_extract,
    send_path_as_zip,
)
from dsync.network.index_diff import (
    Role,
    diff_indexes,
    renames_from_yaml,
    renames_to_yaml,
)
from dsync.network.index_exchange import FolderIndex, IndexExchange
from dsync.network.sync_errors import (
    ErrorCode,
    PeerReportedError,
    SyncError,
    notify_peer,
)
from dsync.state import AppState

from .p2p_core import (
    MsgType,
    async_recv_auth_msg,
    async_recv_msg,
    async_recv_rename,
    async_send_msg,
    async_send_rename,
    create_tls_context,
    get_tls_channel_binding,
)

_SPKI_SIZE = 294  # RSA-2048 SubjectPublicKeyInfo DER
_SIG_SIZE = 256  # RSA-2048 PSS signature


def _classify_local_error(exc: BaseException) -> SyncError | None:
    """Map a locally raised exception to a :class:`SyncError` worth sending.

    Returns ``None`` if the peer cannot meaningfully act on it (network drop,
    timeout, internal bug — those collapse into INTERNAL on the receiving side
    anyway, and at that point our socket is usually gone).
    """
    if isinstance(exc, PeerReportedError):
        # Don't echo a peer's error back at them.
        return None
    if isinstance(exc, PeerAuthError):
        msg = str(exc)
        if "Unknown device" in msg:
            return SyncError(code=ErrorCode.UNKNOWN_DEVICE, message=msg)
        if "path-unsafe" in msg:
            return SyncError(code=ErrorCode.UNSAFE_PEER_ID, message=msg)
        if "RSA" in msg:
            return SyncError(code=ErrorCode.NON_RSA_KEY, message=msg)
        if "has no entry for it" in msg:
            return SyncError(code=ErrorCode.FOLDER_NOT_CONFIGURED, message=msg)
        if "Mode mismatch" in msg or "invalid direction" in msg or "unsupported mode" in msg:
            return SyncError(code=ErrorCode.MODE_MISMATCH, message=msg)
        if "Recursive flag mismatch" in msg:
            return SyncError(code=ErrorCode.RECURSIVE_MISMATCH, message=msg)
        if "not whitelisted" in msg:
            return SyncError(code=ErrorCode.DEVICE_NOT_WHITELISTED, message=msg)
        if "exactly one folder per sync session" in msg:
            return SyncError(code=ErrorCode.MULTIPLE_FOLDERS, message=msg)
        return SyncError(code=ErrorCode.BAD_SIGNATURE, message=msg)
    if isinstance(exc, ConfigConflictError):
        return None  # Already sent by ConfigExchange before raising.
    return None


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
        own_fingerprint = hashlib.sha256(self._own_spki).hexdigest()
        self._own_device_id = self.trusted_devices.get(own_fingerprint, own_fingerprint)

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
        """Raise PeerAuthError if signature does not verify against channel_binding."""
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
            raise PeerAuthError(f"Peer signature invalid: {exc}") from exc

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
                raise PeerAuthError(f"[-] Unknown device! Fingerprint: {fingerprint}")  # noqa: TRY301

            peer_public_key = load_der_public_key(peer_spki)
            if not isinstance(peer_public_key, RSAPublicKey):
                raise PeerAuthError("Peer cert must use RSA key")  # noqa: TRY301
            self._verify_peer_signature(peer_public_key, channel_binding, peer_sig)

            peer_id = self.trusted_devices[fingerprint]
            if "/" in peer_id or "\\" in peer_id or peer_id in {".", ".."}:
                raise PeerAuthError(f"Refusing path-unsafe peer id: {peer_id!r}")  # noqa: TRY301
            print(f"[+] Verified: {peer_id}")

            # Handshake
            if self.is_server:
                await async_send_msg(
                    writer, MsgType.HELLO, b"Hello from server. Data sync can start."
                )
                msg_type, answer = await async_recv_msg(reader)
                if answer is None:
                    raise PeerAuthError("Client closed connection before handshake reply.")  # noqa: TRY301
                if msg_type == MsgType.ERROR:
                    raise PeerReportedError(SyncError.from_yaml(answer))  # noqa: TRY301
                if msg_type != MsgType.HELLO:
                    raise PeerAuthError(  # noqa: TRY301
                        f"Expected hello (type {MsgType.HELLO}), got type {msg_type}"
                    )
                print(f"[*] Message from client: {answer.decode('utf-8')}")
            else:
                msg_type, msg = await async_recv_msg(reader)
                if msg is None:
                    raise PeerAuthError("Server closed connection before handshake greeting.")  # noqa: TRY301
                if msg_type == MsgType.ERROR:
                    raise PeerReportedError(SyncError.from_yaml(msg))  # noqa: TRY301
                if msg_type != MsgType.HELLO:
                    raise PeerAuthError(  # noqa: TRY301
                        f"Expected hello (type {MsgType.HELLO}), got type {msg_type}"
                    )
                print(f"[*] Message from server: {msg.decode('utf-8')}")
                await async_send_msg(writer, MsgType.HELLO, b"Hello from client. I'm ready.")

            await self.start_sync(reader, writer, peer_id)

        except PeerReportedError as e:
            # Peer aborted and told us why — show their reason verbatim.
            print(f"[!] Sync aborted by peer: {e.sync_error.format()}")
        except Exception as e:
            sync_err = _classify_local_error(e)
            if sync_err is not None:
                await notify_peer(writer, sync_err)
                print(f"[!] Sync aborted locally: {sync_err.format()}")
            else:
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
        """Exchange config and folder indexes, diff them, then transfer the folder as a zip.

        Four phases run sequentially, each completing fully before the next starts:
          1. Config exchange and validation: Both sides send their folder config.
          2. Index exchange: Both sides build and exchange a snapshot of the folder's contents.
          3. Index diff: Compare the indexes and determine the necessary transfers.
          4. Payload transfer (existing; not yet driven by the index diff).
        """
        config_to_exchange = (
            FoldersConfig(entries=[self.folder]) if self.folder else self.state.folders
        )
        exchange = ConfigExchange(config_to_exchange, self._own_device_id)

        if self.is_server:
            peer_config = await exchange.exchange_and_validate(
                writer, reader, peer_id, is_source=False
            )

            if len(peer_config.entries) != 1:
                raise PeerAuthError(
                    f"device '{peer_id}' sent {len(peer_config.entries)} folder entries; "
                    f"exactly one folder per sync session is required"
                )

            remote_entry = peer_config.entries[0]
            local_entry = next(
                (e for e in self.state.folders.entries if e.id == remote_entry.id),
                None,
            )
            if not local_entry:
                raise PeerAuthError(
                    f"folder '{remote_entry.id}': device '{peer_id}' wants to sync this "
                    f"folder but device '{self._own_device_id}' has no entry for it"
                )
            validate_peer_folder_config(local_entry, remote_entry, peer_id, self._own_device_id)

            print(f"[+] Config validated for folder '{local_entry.id}'")

            target = Path(local_entry.path)
            is_file_target = target.is_file() or (target.suffix and not target.exists())
            dest_root = target.parent if is_file_target else target
            dest_root.mkdir(parents=True, exist_ok=True)

            local_index = await FolderIndex.build(local_entry.id, target, local_entry.recursive)
            index_exchange = IndexExchange(local_index)
            # Captured for the upcoming diff/selective-transfer step.
            peer_index = await index_exchange.exchange(writer, reader, is_source=False)

            diff = diff_indexes(local_index, peer_index, role=Role.RECEIVER)
            print(
                f"[*] Diff: {len(diff.to_download)} to download, "
                f"{len(diff.unchanged)} unchanged (skipped)"
            )

            # The source always sends a RENAME frame (possibly empty) right
            # after the diff point; read and apply it before any payload.
            rename_payload = await async_recv_rename(reader)
            try:
                renames = renames_from_yaml(rename_payload)
            except (KeyError, TypeError, yaml.YAMLError) as e:
                raise FrameValidationError(f"Failed to parse peer rename commands: {e}") from e
            if renames:
                applied = await apply_renames(renames, dest_root)
                print(f"[+] applied {applied} rename(s) in {dest_root}")

            if not diff.to_download:
                print(f"[+] '{local_entry.id}' already up to date, nothing to transfer")
            else:
                await recv_folder_and_extract(reader, writer, dest_root)
                print(f"[+] '{local_entry.id}' received and extracted to {dest_root}")
        else:
            if not self.folder:
                raise ValueError("Client mode requires folder to be set")

            await exchange.exchange_and_validate(writer, reader, peer_id, is_source=True)

            local_index = await FolderIndex.build(
                self.folder.id, Path(self.folder.path), self.folder.recursive
            )
            index_exchange = IndexExchange(local_index)
            peer_index = await index_exchange.exchange(writer, reader, is_source=True)

            diff = diff_indexes(local_index, peer_index, role=Role.SOURCE)
            print(
                f"[*] Diff: '{len(diff.to_upload)}' to upload, "
                f"{len(diff.unchanged)} unchanged (skipped)"
            )

            # Send rename commands for files that only moved (no re-upload).
            # Always send a frame, even an empty one, so the receiver's
            # unconditional recv stays in lockstep with the wire.
            await async_send_rename(writer, renames_to_yaml(diff.renamed))
            if diff.renamed:
                print(f"[+] sent {len(diff.renamed)} rename command(s)")

            if not diff.to_upload:
                print(f"[+] '{self.folder.id}' already up to date, nothing to transfer")
            else:
                await send_path_as_zip(writer, reader, self.folder)
