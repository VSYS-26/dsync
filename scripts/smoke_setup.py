"""Set up a one-host three-terminal smoke test for the relay+P2P flow.

Run:

    .venv/bin/python scripts/smoke_setup.py /tmp/dsync-smoke

Creates::

    <root>/
        certs/{relay,peer-a,peer-b}-{cert,key}.pem
        peer-a/dsync-config/{relays,devices,folders}.yaml
        peer-a/src-folder/hello.txt
        peer-a/recv-files/
        peer-b/dsync-config/{relays,devices,folders}.yaml
        peer-b/recv-files/

After running, the script prints the four terminal commands needed to
exercise the full flow: relay serve, two `relay connect` daemons, and a
final `sync run_backup`.

The smoke uses a fixed relay port (default 19000) so the peers' relays.yaml
can be written ahead of time. Two daemons running on the same host would
otherwise stomp on the same ``relay.current`` IPC pointer file, so each
peer's terminal must set a distinct ``XDG_RUNTIME_DIR`` (the script tells
you exactly which).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
from pathlib import Path
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _write_self_signed(cert_path: Path, key_path: Path, *, cn: str) -> str:
    """Generate an RSA-2048 self-signed cert+key. Returns the SPKI hex fingerprint."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30))
        .sign(private_key, hashes.SHA256())
    )
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    spki = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(spki).hexdigest()


def _write_yaml(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def build_smoke(root: Path, *, relay_port: int) -> dict[str, object]:
    """Lay out files; return paths + fingerprints useful for the printed commands."""
    root.mkdir(parents=True, exist_ok=True)
    certs = root / "certs"
    certs.mkdir(exist_ok=True)

    relay_fp = _write_self_signed(
        certs / "relay-cert.pem",
        certs / "relay-key.pem",
        cn="dsync-smoke-relay",
    )
    peer_a_fp = _write_self_signed(
        certs / "peer-a-cert.pem",
        certs / "peer-a-key.pem",
        cn="dsync-smoke-peer-a",
    )
    peer_b_fp = _write_self_signed(
        certs / "peer-b-cert.pem",
        certs / "peer-b-key.pem",
        cn="dsync-smoke-peer-b",
    )

    # ---------- peer-A ----------
    peer_a = root / "peer-a"
    (peer_a / "dsync-config").mkdir(parents=True, exist_ok=True)
    src_folder = peer_a / "src-folder"
    src_folder.mkdir(exist_ok=True)
    (src_folder / "hello.txt").write_text(
        "Hello from the dsync smoke test!\nThis file should land on peer-B.\n",
        encoding="utf-8",
    )
    (peer_a / "recv-files").mkdir(exist_ok=True)

    _write_yaml(
        peer_a / "dsync-config" / "relays.yaml",
        f"""relays:
  - id: relay-test
    host: 127.0.0.1
    port: {relay_port}
    fingerprint: {relay_fp}
""",
    )
    _write_yaml(
        peer_a / "dsync-config" / "devices.yaml",
        f"""trusted_devices:
  - id: peer-b
    fingerprint: {peer_b_fp}
    relay_id: relay-test
""",
    )
    _write_yaml(
        peer_a / "dsync-config" / "folders.yaml",
        f"""entries:
  - id: demo
    path: {src_folder}
    mode: backup-to-peer
    devices:
      - peer-b
""",
    )

    # ---------- peer-B ----------
    peer_b = root / "peer-b"
    (peer_b / "dsync-config").mkdir(parents=True, exist_ok=True)
    (peer_b / "recv-files").mkdir(exist_ok=True)

    _write_yaml(
        peer_b / "dsync-config" / "relays.yaml",
        f"""relays:
  - id: relay-test
    host: 127.0.0.1
    port: {relay_port}
    fingerprint: {relay_fp}
""",
    )
    _write_yaml(
        peer_b / "dsync-config" / "devices.yaml",
        f"""trusted_devices:
  - id: peer-a
    fingerprint: {peer_a_fp}
    relay_id: relay-test
""",
    )
    # peer-B has no folders.yaml entries; it only receives.
    _write_yaml(
        peer_b / "dsync-config" / "folders.yaml",
        """entries: []
""",
    )

    return {
        "root": root,
        "relay_port": relay_port,
        "relay_fp": relay_fp,
        "peer_a_fp": peer_a_fp,
        "peer_b_fp": peer_b_fp,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "root",
        type=Path,
        nargs="?",
        default=Path("/tmp/dsync-smoke"),
        help="Directory to lay out the smoke fixtures in (default: /tmp/dsync-smoke).",
    )
    parser.add_argument(
        "--relay-port",
        type=int,
        default=19000,
        help="UDP port for the relay (default: 19000).",
    )
    args = parser.parse_args()

    info = build_smoke(args.root.resolve(), relay_port=args.relay_port)
    root = info["root"]
    relay_port = info["relay_port"]

    py = ".venv/bin/python"
    print(f"\n[+] Smoke fixtures written under {root}\n")
    print("    relay-fingerprint :", info["relay_fp"])
    print("    peer-a fingerprint:", info["peer_a_fp"])
    print("    peer-b fingerprint:", info["peer_b_fp"])
    print()
    print("Now run these four commands, each in its own terminal:\n")

    print("─" * 72)
    print("Terminal 1  —  relay")
    print("─" * 72)
    print(
        f"  cd {Path.cwd()}\n"
        f"  {py} -m dsync.main relay serve \\\n"
        f"      --host 127.0.0.1 --port {relay_port} \\\n"
        f"      --cert {root}/certs/relay-cert.pem \\\n"
        f"      --key  {root}/certs/relay-key.pem"
    )
    print()

    print("─" * 72)
    print("Terminal 2  —  peer-A daemon")
    print("─" * 72)
    print(
        f"  cd {Path.cwd()}\n"
        f"  export XDG_RUNTIME_DIR={root}/runtime-a\n"
        f'  mkdir -p "$XDG_RUNTIME_DIR"\n'
        f"  {py} -m dsync.main \\\n"
        f"      -c {root}/peer-a/dsync-config \\\n"
        f"      relay connect relay-test \\\n"
        f"      --cert {root}/certs/peer-a-cert.pem \\\n"
        f"      --key  {root}/certs/peer-a-key.pem \\\n"
        f"      --recv-dir {root}/peer-a/recv-files"
    )
    print()

    print("─" * 72)
    print("Terminal 3  —  peer-B daemon")
    print("─" * 72)
    print(
        f"  cd {Path.cwd()}\n"
        f"  export XDG_RUNTIME_DIR={root}/runtime-b\n"
        f'  mkdir -p "$XDG_RUNTIME_DIR"\n'
        f"  {py} -m dsync.main \\\n"
        f"      -c {root}/peer-b/dsync-config \\\n"
        f"      relay connect relay-test \\\n"
        f"      --cert {root}/certs/peer-b-cert.pem \\\n"
        f"      --key  {root}/certs/peer-b-key.pem \\\n"
        f"      --recv-dir {root}/peer-b/recv-files"
    )
    print()

    print("─" * 72)
    print("Terminal 4  —  trigger the sync (one-shot, uses peer-A's daemon)")
    print("─" * 72)
    print(
        f"  cd {Path.cwd()}\n"
        f"  export XDG_RUNTIME_DIR={root}/runtime-a\n"
        f"  {py} -m dsync.main \\\n"
        f"      -c {root}/peer-a/dsync-config \\\n"
        f"      sync run_backup"
    )
    print()

    print("─" * 72)
    print("Expected outcome")
    print("─" * 72)
    print(
        f"  {root}/peer-b/recv-files/peer-a/hello.txt  ←  same bytes as\n"
        f"  {root}/peer-a/src-folder/hello.txt\n"
    )
    print("Verify:")
    print(
        f"  diff {root}/peer-a/src-folder/hello.txt \\\n"
        f"       {root}/peer-b/recv-files/peer-a/hello.txt"
    )
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
