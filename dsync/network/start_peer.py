import threading
import time
import socket
from node import P2PNode

TRUSTED_DEVICES = {
    "ADD_FINGERPRINT_FROM_PEER_B": "Peer B (Laptop)",
    "ADD_FINGERPRINT_FROM_PEER_A": "Peer A (Desktop)",
}

def start_server_peer():
    node = P2PNode(is_server=True, cert_path="peer_a_cert.pem", key_path="peer_a_key.pem", trusted_devices=TRUSTED_DEVICES)

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(('127.0.0.1', 9999))
    server_sock.listen(1)
    print("Peer A waits on connections...")

    raw_socket, addr = server_sock.accept()
    node.handle_secure_connection(raw_socket)

def start_client_peer():
    time.sleep(1)
    node = P2PNode(is_server=False, cert_path="peer_b_cert.pem", key_path="peer_b_key.pem", trusted_devices=TRUSTED_DEVICES)

    raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_socket.connect(('127.0.0.1', 9999))

    node.handle_secure_connection(raw_socket)

if __name__ == "__main__":
    t1 = threading.Thread(target=start_server_peer)
    t2 = threading.Thread(target=start_client_peer)

    t1.start()
    t2.start()

    t1.join()
    t2.join()
