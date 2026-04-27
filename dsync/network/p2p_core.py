import ssl
import hashlib
import struct
import socket

from typing import Tuple, Optional, Union
from cryptography import x509 
from cryptography.hazmat.primitives import serialization

def send_msg(sock: Union[socket.socket, ssl.SSLSocket], msg_type: int, data: bytes) -> None:
    '''
    Sends a message with a length prefix and type.
    
    Args:
        sock: The socket to send data over.
        msg_type: The integer message type.
        data: The payload in bytes.
    '''
    # ! = Network Byte Order, B = unsigned char, I = unsigned int
    header = struct.pack("!BI", msg_type, len(data))
    sock.sendall(header + data)

def recv_msg(sock: Union[socket.socket, ssl.SSLSocket]) -> Tuple[Optional[int], Optional[bytes]]:
    '''
    Receives a message exactly based on its length.
    
    Args:
        sock: The socket to read data from.

    Returns:
        Tuple containing the message type and the payload.
    '''
    header = sock.recv(5)
    if not header or len(header) < 5:
        return None, None
    
    msg_type, length = struct.unpack("!BI", header)

    chunks = []
    bytes_recvd = 0
    while bytes_recvd < length:
        chunk = sock.recv(min(length - bytes_recvd, 4096))
        if not chunk:
            raise RuntimeError("Connection lost during reception.")
        chunks.append(chunk)
        bytes_recvd += len(chunk)
          
    return msg_type, b"".join(chunks)

def get_public_key_fingerprint(cert_der: bytes) -> str:
    '''
    Extracts the public key from an .x509 certificate in DER format
    and calculates an SHA-256 fingerprint from it.

    The fingerprint can be used to uniquely identify a certificate or a device
    based on its public key and compare it with a list of trusted keys.

    Args: 
        cert_der: The certificate as DER-encoded binary data.

    Returns:
        str: SHA-256 hash of the public key as a hex string.
    '''
    # Load certificate from DER binary data
    cert = x509.load_der_x509_certificate(cert_der)

    # Extract public key from the certificate
    public_key = cert.public_key()

    public_key_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )

    return hashlib.sha256(public_key_bytes).hexdigest()


def create_tls_context(is_server: bool, cert_path: str, key_path: str) -> ssl.SSLContext:
    '''
    Creates the SSL context for the client or server.
    '''
    purpose = ssl.Purpose.CLIENT_AUTH if is_server else ssl.Purpose.SERVER_AUTH
    context = ssl.create_default_context(purpose)

    # Upload your own certificate to verify your identity
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)

    # Disable CA security checks
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    return context
