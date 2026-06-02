import struct

import pytest

from dsync.network.p2p_core import (
    MAX_CONFIG_SIZE,
    MsgType,
    async_recv_auth_msg,
    async_recv_config,
    async_recv_config_ack,
    async_recv_msg,
    async_send_config,
    async_send_config_ack,
    async_send_msg,
    get_public_key_fingerprint,
)


async def test_send_recv_roundtrip(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    await async_send_msg(writer_a, MsgType.HELLO, b"hello-payload")
    msg_type, data = await async_recv_msg(reader_b)
    assert msg_type == MsgType.HELLO
    assert data == b"hello-payload"


async def test_send_recv_empty_payload(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    await async_send_msg(writer_a, MsgType.CONFIG_ACK, b"")
    msg_type, data = await async_recv_msg(reader_b)
    assert msg_type == MsgType.CONFIG_ACK
    assert data == b""


async def test_recv_returns_none_on_eof(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    writer_a.close()
    msg_type, data = await async_recv_msg(reader_b)
    assert msg_type is None
    assert data is None


async def test_framing_wire_format(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _) = stream_pair
    payload = b"test"
    await async_send_msg(writer_a, MsgType.FILE_META, payload)
    raw = await reader_b.readexactly(5 + len(payload))
    msg_type, length = struct.unpack("!BI", raw[:5])
    assert msg_type == MsgType.FILE_META
    assert length == len(payload)
    assert raw[5:] == payload


async def test_recv_auth_msg_accepts_correct_auth(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    payload = b"x" * 550
    await async_send_msg(writer_a, MsgType.AUTH, payload)
    received = await async_recv_auth_msg(reader_b)
    assert received == payload


async def test_recv_auth_msg_rejects_wrong_type(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    await async_send_msg(writer_a, MsgType.HELLO, b"x" * 550)
    with pytest.raises(RuntimeError, match="Expected auth message"):
        await async_recv_auth_msg(reader_b)


async def test_recv_auth_msg_rejects_wrong_size(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    await async_send_msg(writer_a, MsgType.AUTH, b"x" * 100)
    with pytest.raises(RuntimeError, match="wrong size"):
        await async_recv_auth_msg(reader_b)


async def test_send_recv_config_roundtrip(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    config_data = b"entries: []\n"
    await async_send_config(writer_a, config_data)
    received = await async_recv_config(reader_b)
    assert received == config_data


async def test_recv_config_rejects_wrong_type(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    await async_send_msg(writer_a, MsgType.HELLO, b"some data")
    with pytest.raises(RuntimeError, match="Expected CONFIG"):
        await async_recv_config(reader_b)


async def test_recv_config_rejects_oversized_payload(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    oversized = MAX_CONFIG_SIZE + 1
    header = struct.pack("!BI", MsgType.CONFIG, oversized)
    writer_a.write(header)
    await writer_a.drain()
    with pytest.raises(RuntimeError, match="too large"):
        await async_recv_config(reader_b)


async def test_send_recv_config_ack_roundtrip(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    await async_send_config_ack(writer_a)
    await async_recv_config_ack(reader_b)


async def test_recv_config_ack_rejects_wrong_type(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    await async_send_msg(writer_a, MsgType.HELLO, b"")
    with pytest.raises(RuntimeError, match="Expected CONFIG_ACK"):
        await async_recv_config_ack(reader_b)


def test_create_tls_context_server(tmp_path) -> None:
    import ssl

    from dsync.crypto.setup_certs import generate_self_signed_cert
    from dsync.network.p2p_core import create_tls_context

    generate_self_signed_cert(str(tmp_path / "cert.pem"), str(tmp_path / "key.pem"))
    ctx = create_tls_context(
        is_server=True,
        cert_path=str(tmp_path / "cert.pem"),
        key_path=str(tmp_path / "key.pem"),
    )
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_NONE


def test_create_tls_context_client(tmp_path) -> None:
    import ssl

    from dsync.crypto.setup_certs import generate_self_signed_cert
    from dsync.network.p2p_core import create_tls_context

    generate_self_signed_cert(str(tmp_path / "cert.pem"), str(tmp_path / "key.pem"))
    ctx = create_tls_context(
        is_server=False,
        cert_path=str(tmp_path / "cert.pem"),
        key_path=str(tmp_path / "key.pem"),
    )
    assert isinstance(ctx, ssl.SSLContext)


async def test_recv_msg_mid_stream_eof_raises(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    writer_a.write(struct.pack("!BI", MsgType.HELLO, 8))
    await writer_a.drain()
    writer_a.close()
    await writer_a.wait_closed()

    with pytest.raises(RuntimeError, match="Connection lost"):
        await async_recv_msg(reader_b)


async def test_recv_auth_msg_eof_before_header_raises(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    writer_a.close()
    await writer_a.wait_closed()

    with pytest.raises(RuntimeError, match="Connection closed before auth"):
        await async_recv_auth_msg(reader_b)


async def test_recv_auth_msg_eof_after_header_raises(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    writer_a.write(struct.pack("!BI", MsgType.AUTH, 550))
    await writer_a.drain()
    writer_a.close()
    await writer_a.wait_closed()

    with pytest.raises(RuntimeError, match="Connection lost during auth"):
        await async_recv_auth_msg(reader_b)


async def test_recv_config_eof_before_header_raises(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    writer_a.close()
    await writer_a.wait_closed()

    with pytest.raises(RuntimeError):
        await async_recv_config(reader_b)


async def test_recv_config_eof_after_header_raises(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    writer_a.write(struct.pack("!BI", MsgType.CONFIG, 16))
    await writer_a.drain()
    writer_a.close()
    await writer_a.wait_closed()

    with pytest.raises(RuntimeError):
        await async_recv_config(reader_b)


async def test_recv_config_ack_eof_raises(stream_pair) -> None:
    (_reader_a, writer_a), (reader_b, _writer_b) = stream_pair
    writer_a.close()
    await writer_a.wait_closed()

    with pytest.raises(RuntimeError):
        await async_recv_config_ack(reader_b)


def test_get_public_key_fingerprint_is_64_hex_chars() -> None:
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "Test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_der = cert.public_bytes(serialization.Encoding.DER)
    fp = get_public_key_fingerprint(cert_der)
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)
