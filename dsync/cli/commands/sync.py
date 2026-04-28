import typer
import yaml
import socket

from pathlib import Path
from typing import Annotated, Optional, Dict

from dsync.cli.console import info, success
from dsync.network.node import P2PNode

app: typer.Typer = typer.Typer()

def load_trusted_devices() -> Dict[str, str]:
    '''
    Loads the list of trusted devices of a local YAML configuration file.

    Searches the path `dsyn_config/devices.yaml` for stored certificate fingerprints.
    This list serves as a whitelist to ensure that, for incoming or outgoing connections,
    communication occurs only with known devices.

    Returns:
        Dict[str, str]: A dictionary with certificate fingerprints as keys and the corresponding
                    device names as values. Returns an empty dictionary if the file does not exist.
    '''
    config_path = Path("dsync_config/devices.yaml")
    if config_path.exists():
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)
            if not isinstance(data, dict):
                return {}

            trusted_devices = data.get("trusted_devices", {})
            if not isinstance(trusted_devices, dict):
                return {}

            return trusted_devices
    return {}

@app.command("start")
def start_p2p_sync(
    mode: Annotated[str, typer.Option(help="'server' (waits) or 'client' (connects)")] = "client",
    relay_host: Annotated[Optional[str], typer.Option(help="IP from relay server")] = None,
    cert: Annotated[str, typer.Option(help="Path to your certificate (.pem)")] = "cert.pem",
    key: Annotated[str, typer.Option(help="Path to your private key (.pem)")] = "key.pem",
    port: Annotated[int, typer.Option(help="The network port")] = 9999,
) -> None:
    '''
    Main CLI command for starting peer-to-peer data synchronization.

    Initializes the P2P node and handles the establishment of the basic (still unencrypted) TCP connection.
    It supports two main modes:

    1. Relay mode: If a `relay_host` is specified, both peers connect to the relay server, which acts as an intermediary.
    2. LAN mode: If no relay is used, the program either acts as a listening server or connects directly.

    Once the raw socket connection is established, it is passed to the 'P2PNode',
    which handles the TLS handshake, authentication, and data exchange.

    Args:
        mode (str): The role in the direct connection ('server' or 'client').
        relay_host (str, optional): The IP address of an external relay server for NAT traversal.
        cert (str): Path to ones own TLS certificate file (.pem).
        key (str): Path to ones own private key file (.pem).
        port (int): The network port for direct connections (local).
    '''

    trusted_devices: Dict[str, str] = load_trusted_devices()
    is_server: bool = (mode.lower() == "server")
    node = P2PNode(is_server, cert, key, trusted_devices)

    raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    if relay_host:
        info(f"Use relay mode via {relay_host}...")
        # Both connect outgoing to relay
        raw_socket.connect((relay_host, 10000))
        success("Connected to relay. Waiting on partner peer...")
    else:
        # LAN mode
        if is_server:
            raw_socket.bind(('0.0.0.0', port))
            raw_socket.listen(1)
            raw_socket, _ = raw_socket.accept()
        else:
            raw_socket.connect(('127.0.0.1', port))
    
    transfer_completed = node.handle_secure_connection(raw_socket)

    if transfer_completed:
        success("Data transfer completed.")
