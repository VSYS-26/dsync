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

## Testing locally

dsync runs as three cooperating processes — a relay plus one **`dsync relay connect`** daemon per peer — and a one-shot **`dsync sync run_backup`** that hands work to the local daemon over a Unix-domain socket. To exercise the full flow on a single host you need **four terminals**: relay, peer-A daemon, peer-B daemon, `sync run_backup`.

A scripted setup and step-by-step walkthrough live in **[`docs/smoke-test.md`](docs/smoke-test.md)**. The TL;DR:

```bash
# 1. Generate fixtures (certs, configs, sample file)
.venv/bin/python scripts/smoke_setup.py /tmp/dsync-smoke
# → prints the exact four terminal commands you need
```

After running the four terminal commands the script printed:

```bash
diff /tmp/dsync-smoke/peer-a/src-folder/hello.txt \
     /tmp/dsync-smoke/peer-b/recv-files/peer-a/hello.txt   # silent = success
```

See `docs/smoke-test.md` for the expected log output, a troubleshooting table, and the list of things this smoke does **not** validate (real-world NAT traversal, daemon long-running stability, Windows IPC).
