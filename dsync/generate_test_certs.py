"""Generate fingerprints for demo."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dsync.crypto.setup_certs import generate_self_signed_cert

print("--- Generate Peer A (Server) ---")
fp_a = generate_self_signed_cert("peer_a_cert.pem", "peer_a_key.pem")

print("\n--- Generate Peer B (Client) ---")
fp_b = generate_self_signed_cert("peer_b_cert.pem", "peer_b_key.pem")

print(f'PEER_A_FINGERPRINT = "{fp_a}"')
print(f'PEER_B_FINGERPRINT = "{fp_b}"')
