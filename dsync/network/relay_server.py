"""TCP relay server for bridging two peers behind NAT/firewall."""

import socket
import threading


def bridge_sockets(source: socket.socket, destination: socket.socket) -> None:
    """Continuously forward data traffic from a source socket to a destination socket.

    This function reads up to 4096 bytes from the source socket in an infinite loop
    and sends them directly to the destination socket.
    The loop is interrupted as soon as the connection is closed (no more data is received)
    or a network error occurs.
    Regardless of how the loop ends, both sockets are safely closed in the 'finally' block.

    Args:
        source (socket.socket): The socket from which data is read.
        destination (socket.socket): The socket to which the forwarded data is sent.
    """
    try:
        while True:
            data = source.recv(4096)
            if not data:
                break
            destination.sendall(data)
    except Exception:  # nosec B110
        pass
    finally:
        source.close()
        destination.close()


def start_relay(port: int = 10000) -> None:
    """Start a TCP relay server that acts as a bridge between two peers.

    The server listens for incoming connections on the specified port.
    Once two clients have connected, two separate threads are started.
    These threads use 'bridge_sockets' to establish a bidirectional bridge:
    - Thread 1: Forwards data from peer 1 to peer 2.
    - Thread 2: Forwards data from peer 2 to peer 1.

    Useful when two devices cannot communicate directly with each other (e.g., NAT or firewall)
    and require a public relay.

    Args:
        port (int, optional): The port on which the relay server should listen. Default: 10 000
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("0.0.0.0", port))  # nosec B104
    server.listen(2)
    print(f"[*] Relay server active on port {port}. Waiting on two peers...")

    while True:
        # Peer 1 connects
        peer1, addr1 = server.accept()
        print(f"[+] Peer 1 connected: {addr1}")

        # Peer 2 connects
        peer2, addr2 = server.accept()
        print(f"[+] Peer 2 connected: {addr2}")

        # Build a two-way bridge
        threading.Thread(target=bridge_sockets, args=(peer1, peer2), daemon=True).start()
        threading.Thread(target=bridge_sockets, args=(peer2, peer1), daemon=True).start()
        print("[*] Bridge active. Data flows...")


if __name__ == "__main__":
    start_relay()
