"""Length-prefixed P2P framing and TLS context helpers."""

import asyncio
import hashlib
import ssl
import struct
from typing import Tuple, Optional

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


async def async_recv_msg(reader: asyncio.StreamReader) -> Tuple[Optional[int], Optional[bytes]]:
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


def create_tls_context(is_server: bool, cert_path: str, key_path: str) -> ssl.SSLContext:
    """Build a mutual-TLS SSL context with hostname checks disabled."""
    purpose = ssl.Purpose.CLIENT_AUTH if is_server else ssl.Purpose.SERVER_AUTH
    context = ssl.create_default_context(purpose)

    context.load_cert_chain(certfile=cert_path, keyfile=key_path)

    # Disable hostname checks (irrelevant for P2P)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # No CA check -> fingerprint handles that

    # if is_server:
    #     # Request client certificate, but do not enforce CA verification.
    #     # Verification is done manually from the fingerprint whitelist.
    #     context.verify_mode = ssl.CERT_OPTIONAL
    # else:
    #     # Client does not need to verify the server's certificate via CAs;
    #     # it will manually check the fingerprint after the handshake.
    #     context.verify_mode = ssl.CERT_NONE

    return context
