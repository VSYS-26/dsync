# dsync

Decentralized file and folder sync between trusted devices - no central server.

A proof-of-concept developed as part of a university project in the course **Distributed Systems**.

---

## Idea

Most file sync solutions rely on a central server or cloud service. This project explores a different approach: devices sync directly with each other, without any intermediary.

Trust between devices is established explicitly and manually. There is no automatic pairing, no account system, and no central coordinator.

---

## Goals

- Sync files and folders directly between trusted devices
- Support two modes: keeping devices in sync (mirror) and backing up to another device
- Keep the design simple and easy to reason about

---

## Status

Work in progress - early proof-of-concept stage.

---

## Development Setup

This project uses [uv](https://docs.astral.sh/uv/) for fast Python dependency management and virtual environments.

### Prerequisites

- [Install uv](https://docs.astral.sh/uv/getting-started/installation/)

### Installation

1. Clone the repository and navigate into the project directory.
2. Sync the project (this automatically creates a virtual environment in `.venv/` and installs all required dependencies):
   ```bash
   uv sync
   ```

### UV commands

- run a python script
  ```bash
  uv run dsync/main.py
  ```

- add a library
  ```bash
  uv add library_name
  ```

- install and run checks
  ```bash
  uv run pre-commit install
  uv run pre-commit install --hook-type commit-msg
  uv run pre-commit run --all-files
  ```

---

## Standards and Tooling

### Python standards

- PEP 8: baseline style, naming, indentation, and structure
- PEP 257: docstring conventions (Google style via Ruff)
- PEP 484: static typing and type-hint checks (mypy strict mode)

### Git standards

- Conventional Commits for commit messages:
  https://www.conventionalcommits.org/
- Commit messages are validated automatically through a `commit-msg` pre-commit hook.

### Enforced checks (pre-commit on each commit)

- Ruff: linting, formatting, import sorting, auto-fixes, docstring checks
- mypy: static type checking
- Bandit: security scanning (secrets, weak crypto, insecure patterns)

---

## Testing locally (Relay Setup)

To test the peer-to-peer connection on your local machine, you need to simulate two devices and one relay server. This requires three separate terminal windows.

#### **1. Generate Certificates & Setup Trust**

Before two peers can communicate, they need their cryptographic identities.

   1. Generate the self-signed certificates and private keys for your nodes. The script will output a SHA-256 fingerprint.
   2. Create a configuration file at dsync_config/devices.yaml.
   3. Add the fingerprints of the trusted devices to this file to authorize them (Mutual TLS authentication):
   ```yaml
   trusted_devices:
      "YOUR_GENERATED_FINGERPRINT_HASH_HERE": "Peer B (Laptop)"
   ```
#### **2. Run the 3 Terminals**

Open three separate terminals in the root directory of the project.

**Terminal 1: Start the Relay Server**

The relay server acts as a bridge to overcome NAT/Firewall restrictions.
```bash
uv run python relay_server.py
```

**Terminal 2: Start Node A (Server mode)**

This node connects to the relay and waits for a trusted partner.
```bash
uv run python -m dsync.main sync start --mode server --relay-host 127.0.0.1
```

**Terminal 3: Start Node B (Client mode)**

This node connects to the relay and initiates the handshake and sync process.
```bash
uv run python -m dsync.main sync start --mode client --relay-host 127.0.0.1
```

If everything is configured correctly, you will see the Mutual TLS verification succeed and the nodes exchanging their file hashes.
