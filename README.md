# dsync

Decentralized file and folder sync between trusted devices – no central server.

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

Work in progress – early proof-of-concept stage.

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

- install & run linter and security checks
```bash
uv run pre-commit install
uv run pre-commit run --all-files
```