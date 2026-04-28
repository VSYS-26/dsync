from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
import datetime
import hashlib

def generate_self_signed_cert(cert_name: str="cert.pem", key_name: str="key.pem") -> None:
    '''
    Generates a self-signed X.509 certificate and a private RSA key.

    Creates the cryptographic identity for the P2P node.
    By default, the certificate is issued in the name of "P2PNode" and is valid for 10 years.
    Both the certificate and the private key are stored in unencrypted PEM format on the hard drive.

    Calculates the SHA-256 fingerprint of the public key and displays it on the console.
    This fingerprint is essential, as it must be entered into the partner's `devices.yaml` (whitelist)
    so that the devices can trust each other.

    Args:
        cert_name (str, optional): The file path/name for the certificate to be saved. Default: cert.pem.
        key_name (str, optional): The file path/name for the private key to be saved. Default: key.pem.
    '''
    # Generate private key
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    
    # Build certificate
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"P2P-Node")])
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        private_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.now(datetime.timezone.utc)
    ).not_valid_after(
        # Valid 10 years
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650)
    ).sign(private_key, hashes.SHA256())
    
    # Save on drive
    with open(key_name, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))
        
    with open(cert_name, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
        
    # Calculate fingerprint for yaml
    public_key_bytes = cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    fingerprint = hashlib.sha256(public_key_bytes).hexdigest()
    
    print(f"[+] Data {cert_name} and {key_name} were created!")
    print(f"[!] Fingerprint for devices.yaml is:\n{fingerprint}")

if __name__ == "__main__":
    generate_self_signed_cert()
