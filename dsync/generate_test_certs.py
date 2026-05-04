"""Generate fingerprints for demo."""

import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.insert(0, parent_dir)

from dsync.crypto.setup_certs import generate_self_signed_cert

print("--- Generate Peer A (Server) ---")
fp_a = generate_self_signed_cert("peer_a_cert.pem", "peer_a_key.pem")

print("\n--- Generate Peer B (Client) ---")
fp_b = generate_self_signed_cert("peer_b_cert.pem", "peer_b_key.pem")

print(f'PEER_A_FINGERPRINT = "{fp_a}"')
print(f'PEER_B_FINGERPRINT = "{fp_b}"')
