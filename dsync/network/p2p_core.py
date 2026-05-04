"""Length-prefixed P2P framing and TLS context helpers."""

import asyncio
import hashlib
import ssl
import struct

from cryptography import x509
from cryptography.hazmat.primitives import serialization


async def async_send_msg(writer: asyncio.StreamWriter, msg_type: int, data: bytes) -> None:
    """
    Sends a message with a length prefix and type.
    """
    # ! = Network Byte Order, B = unsigned char, I = unsigned int
    header = struct.pack("!BI", msg_type, len(data))
    writer.write(header + data)
    await writer.drain()


async def async_recv_msg(reader: asyncio.StreamReader) -> tuple[int | None, bytes | None]:
    """
    Receives a message exactly based on its length.
    """
    try:
        header = await reader.readexactly(5)
    except asyncio.IncompleteReadError:
        return None, None

    msg_type, length = struct.unpack("!BI", header)

    try:
        data = await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        raise RuntimeError("Connection lost during reception.")

    return msg_type, data


# RSA-2048 SubjectPublicKeyInfo DER (294 B) + RSA-2048 PSS signature (256 B)
_AUTH_PAYLOAD_SIZE = 550


async def async_recv_auth_msg(reader: asyncio.StreamReader) -> bytes:
    """Receive a fixed-size auth message (type 0).

    Rejects wrong msg_type or size mismatch before any payload allocation.
    Returns the raw 550-byte payload: SPKI bytes || signature bytes.
    """
    try:
        header = await reader.readexactly(5)
    except asyncio.IncompleteReadError:
        raise RuntimeError("Connection closed before auth message received.")

    msg_type, length = struct.unpack("!BI", header)

    if msg_type != 0:
        raise RuntimeError(f"Expected auth message (type 0), got type {msg_type}")

    if length != _AUTH_PAYLOAD_SIZE:
        raise RuntimeError(f"Auth message wrong size: got {length} B, expected {_AUTH_PAYLOAD_SIZE} B")

    try:
        return await reader.readexactly(_AUTH_PAYLOAD_SIZE)
    except asyncio.IncompleteReadError:
        raise RuntimeError("Connection lost during auth message reception.")


def get_public_key_fingerprint(cert_der: bytes) -> str:
    """Compute the SHA-256 fingerprint of the public key inside a DER cert.

    Extracts the public key from an X.509 certificate in DER format and
    calculates an SHA-256 fingerprint from it.

    The fingerprint can be used to uniquely identify a certificate or a device
    based on its public key and compare it with a list of trusted keys.

    Args:
        cert_der: The certificate as DER-encoded binary data.

    Returns:
        str: SHA-256 hash of the public key as a hex string.
    """
    # Load certificate from DER binary data
    cert = x509.load_der_x509_certificate(cert_der)

    # Extract public key from the certificate
    public_key = cert.public_key()

    public_key_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.DER, format=serialization.PublicFormat.SubjectPublicKeyInfo
    )

    return hashlib.sha256(public_key_bytes).hexdigest()


def get_tls_channel_binding(writer: asyncio.StreamWriter) -> bytes:
    """Derive 32 bytes of TLS session-unique material for app-layer auth.

    Prevents MITM relay: attacker relaying cert bytes between two tunnels cannot
    forge a signature over the victim-side binding without the victim's private key.

    Python 3.13+: uses TLS exporter (RFC 5705) via export_keying_material.
    Python <3.13:  SSLObject lacks export_keying_material; falls back to tls-unique
                   channel binding (RFC 5929), which requires TLS 1.2.
    """
    ssl_object = writer.get_extra_info("ssl_object")
    if ssl_object is None:
        raise RuntimeError("No SSL object on writer — connection is not TLS")
    if hasattr(ssl_object, "export_keying_material"):
        return ssl_object.export_keying_material("dsync-peer-auth-v1", 32, context=None)
    binding = ssl_object.get_channel_binding("tls-unique")
    if binding is None:
        raise RuntimeError(
            "TLS channel binding unavailable — Python 3.13+ required for TLS 1.3, "
            "or TLS 1.2 must be negotiated (see create_tls_context)"
        )
    return hashlib.sha256(binding).digest()


def create_tls_context(is_server: bool, cert_path: str, key_path: str) -> ssl.SSLContext:
    """Build a TLS context with hostname checks and CA validation disabled.

    CERT_NONE is required for self-signed certs (no shared CA). MITM resistance
    is provided by the application-layer auth: each side signs the session's
    TLS channel binding with its RSA private key, so an attacker relaying cert
    bytes between two tunnels cannot forge the signature over the victim's
    channel binding without the private key.
    """
    purpose = ssl.Purpose.CLIENT_AUTH if is_server else ssl.Purpose.SERVER_AUTH
    context = ssl.create_default_context(purpose)

    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    if not hasattr(ssl.SSLObject, "export_keying_material"):
        # tls-unique channel binding (used as fallback on Python <3.13) requires TLS 1.2
        context.maximum_version = ssl.TLSVersion.TLSv1_2

    return context
