import hashlib
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from dsync.crypto.setup_certs import generate_self_signed_cert


def test_creates_cert_and_key_files(tmp_path: Path) -> None:
    generate_self_signed_cert(str(tmp_path / "cert.pem"), str(tmp_path / "key.pem"))
    assert (tmp_path / "cert.pem").exists()
    assert (tmp_path / "key.pem").exists()


def test_cert_is_parseable_x509(tmp_path: Path) -> None:
    cert_path = tmp_path / "cert.pem"
    generate_self_signed_cert(str(cert_path), str(tmp_path / "key.pem"))
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    assert cert.subject is not None


def test_cert_is_self_signed(tmp_path: Path) -> None:
    cert_path = tmp_path / "cert.pem"
    generate_self_signed_cert(str(cert_path), str(tmp_path / "key.pem"))
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    assert cert.subject == cert.issuer


def test_cert_validity_period_approx_10_years(tmp_path: Path) -> None:
    cert_path = tmp_path / "cert.pem"
    generate_self_signed_cert(str(cert_path), str(tmp_path / "key.pem"))
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    delta = cert.not_valid_after_utc - cert.not_valid_before_utc
    assert abs(delta.days - 3650) <= 2


def test_private_key_is_rsa_2048(tmp_path: Path) -> None:
    key_path = tmp_path / "key.pem"
    generate_self_signed_cert(str(tmp_path / "cert.pem"), str(key_path))
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    assert isinstance(key, rsa.RSAPrivateKey)
    assert key.key_size == 2048


def test_fingerprint_printed_matches_cert_public_key(tmp_path: Path, capsys) -> None:
    cert_path = tmp_path / "cert.pem"
    generate_self_signed_cert(str(cert_path), str(tmp_path / "key.pem"))

    captured = capsys.readouterr()
    printed_fp = captured.out.strip().splitlines()[-1].strip()

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    pub_bytes = cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    expected_fp = hashlib.sha256(pub_bytes).hexdigest()
    assert printed_fp == expected_fp
