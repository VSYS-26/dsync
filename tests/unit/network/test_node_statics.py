from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import pytest

from dsync.network.node import P2PNode


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_pack_unpack_roundtrip() -> None:
    spki = b"s" * 294
    sig = b"g" * 256
    packed = P2PNode._pack_auth_msg(spki, sig)
    assert len(packed) == 550
    unpacked_spki, unpacked_sig = P2PNode._unpack_auth_msg(packed)
    assert unpacked_spki == spki
    assert unpacked_sig == sig


def test_pack_auth_msg_concatenates() -> None:
    spki = bytes(i % 256 for i in range(294))
    sig = b"\xff" * 256
    packed = P2PNode._pack_auth_msg(spki, sig)
    assert packed[:294] == spki
    assert packed[294:] == sig


def test_verify_peer_signature_valid(rsa_key) -> None:
    binding = b"channel-binding-32-bytes-padding!"
    sig = rsa_key.sign(
        binding,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    P2PNode._verify_peer_signature(rsa_key.public_key(), binding, sig)


def test_verify_peer_signature_wrong_binding_raises(rsa_key) -> None:
    binding = b"correct-binding"
    sig = rsa_key.sign(
        binding,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    with pytest.raises(ValueError, match="invalid"):
        P2PNode._verify_peer_signature(rsa_key.public_key(), b"wrong-binding", sig)


def test_verify_peer_signature_garbage_sig_raises(rsa_key) -> None:
    with pytest.raises(ValueError, match="invalid"):
        P2PNode._verify_peer_signature(rsa_key.public_key(), b"binding", b"garbage" * 36)


def test_unpack_auth_msg_spki_size() -> None:
    payload = b"x" * 550
    spki, sig = P2PNode._unpack_auth_msg(payload)
    assert len(spki) == 294
    assert len(sig) == 256
