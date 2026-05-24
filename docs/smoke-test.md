# Smoke test — relay + two peers + sync, on one host

This guide walks through the full new flow end-to-end on a single host
using four terminals. If every step works, you've manually validated:

* `dsync relay serve` (the QUIC rendezvous server),
* `dsync relay connect` (the long-running per-peer daemon, twice),
* `dsync sync run_backup` (the one-shot CLI that hands work to the daemon
  over Unix-domain-socket IPC),
* `relay_daemon` → `relay` → `relay_daemon` matchmaking + a direct
  peer-to-peer QUIC session on the multiplexed socket.

The test transfers one small text file (`hello.txt`) from peer A's
`src-folder` to peer B's `recv-files` directory.

## Prerequisites

* The repository checked out at `/home/timo/Development/vsys`
  (or any path — every command below uses absolute paths from
  `scripts/smoke_setup.py`'s output).
* `uv sync` has run at least once so `.venv/bin/python` exists. If not:

  ```bash
  uv sync
  ```

* Four free terminals.

## 1. Generate fixtures

Run the setup script once. It creates three RSA-2048 self-signed cert
pairs (relay, peer-A, peer-B), writes the two peers' `dsync-config/`
directories with matching fingerprints, and drops a small file into
peer-A's source folder.

```bash
.venv/bin/python scripts/smoke_setup.py /tmp/dsync-smoke
```

The script prints the four terminal commands you need next (so you don't
have to copy them out of this file by hand) along with the
fingerprints for sanity-checking.

## 2. Start the relay  (terminal 1)

```bash
cd /home/timo/Development/vsys
.venv/bin/python -m dsync.main relay serve \
    --host 127.0.0.1 --port 19000 \
    --cert /tmp/dsync-smoke/certs/relay-cert.pem \
    --key  /tmp/dsync-smoke/certs/relay-key.pem
```

Expected output:

```
Relay fingerprint: <hex>
Binding 127.0.0.1:19000 ...
Relay listening on UDP port 19000
```

Leave this running.

## 3. Start peer-A's daemon  (terminal 2)

Peer-A is the **source** in this test (holds the file we'll send). Each
daemon must use a distinct `XDG_RUNTIME_DIR` so its IPC pointer file
(`relay.current`) doesn't get clobbered by peer-B's daemon.

```bash
cd /home/timo/Development/vsys
export XDG_RUNTIME_DIR=/tmp/dsync-smoke/runtime-a
mkdir -p "$XDG_RUNTIME_DIR"
.venv/bin/python -m dsync.main \
    -c /tmp/dsync-smoke/peer-a/dsync-config \
    relay connect relay-test \
    --cert /tmp/dsync-smoke/certs/peer-a-cert.pem \
    --key  /tmp/dsync-smoke/certs/peer-a-key.pem \
    --recv-dir /tmp/dsync-smoke/peer-a/recv-files
```

Expected log lines:

```
Connecting to relay 'relay-test' at 127.0.0.1:19000...
INFO dsync.network.relay_daemon: registered to relay; observed endpoint 127.0.0.1:<port>
INFO dsync.network.local_ipc:    LocalControlServer bound on …/runtime-a/dsync/relay-<pid>.sock
INFO dsync.network.relay_daemon: RelayDaemon up: own fp=…, relay=127.0.0.1:19000, …
```

Leave this running.

## 4. Start peer-B's daemon  (terminal 3)

Peer-B is the **target** (the file lands here). Different
`XDG_RUNTIME_DIR`, different config directory.

```bash
cd /home/timo/Development/vsys
export XDG_RUNTIME_DIR=/tmp/dsync-smoke/runtime-b
mkdir -p "$XDG_RUNTIME_DIR"
.venv/bin/python -m dsync.main \
    -c /tmp/dsync-smoke/peer-b/dsync-config \
    relay connect relay-test \
    --cert /tmp/dsync-smoke/certs/peer-b-cert.pem \
    --key  /tmp/dsync-smoke/certs/peer-b-key.pem \
    --recv-dir /tmp/dsync-smoke/peer-b/recv-files
```

You should see the same `registered to relay` / `RelayDaemon up` pair
of log lines as for peer-A, with a different observed port.

At this point the relay's terminal should have logged two
`peer registered:` lines — one per daemon.

## 5. Trigger the sync  (terminal 4)

This is the one-shot command. It must run with peer-A's
`XDG_RUNTIME_DIR` so it discovers peer-A's daemon (not peer-B's) over
the IPC pointer file.

```bash
cd /home/timo/Development/vsys
export XDG_RUNTIME_DIR=/tmp/dsync-smoke/runtime-a
.venv/bin/python -m dsync.main \
    -c /tmp/dsync-smoke/peer-a/dsync-config \
    sync run_backup
```

Expected output:

```
Syncing all 1 configured folder(s) via daemon...

Folder 'demo' → 1 peer(s)
   synced

============================================================
Completed: 1 successful sync(s)
```

Behind the scenes, the daemon logs will show:

* peer-A: `verified peer peer-b` → `source sending 1 file(s) to peer-b`
* peer-B: `expecting inbound dial from peer-a` →
  `verified peer peer-a` → `peer receiving files from peer-a` →
  `inbound sync from peer-a complete`

## 6. Verify the file landed

```bash
diff /tmp/dsync-smoke/peer-a/src-folder/hello.txt \
     /tmp/dsync-smoke/peer-b/recv-files/peer-a/hello.txt
```

`diff` exits silently if the files match. If you also want to see the
contents:

```bash
cat /tmp/dsync-smoke/peer-b/recv-files/peer-a/hello.txt
```

## 7. Tear down

Ctrl-C in each of terminals 1, 2, and 3 (in any order). Then:

```bash
rm -rf /tmp/dsync-smoke
```

Re-running the setup script will recreate everything from scratch.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `relay-test is not configured in relays.yaml` from a daemon | You're pointing `-c` at the wrong config directory. |
| `no running relay-connect daemon` from `sync run_backup` | `XDG_RUNTIME_DIR` doesn't match peer-A's daemon, or the daemon isn't running, or the pointer file got clobbered (re-run with different `XDG_RUNTIME_DIR` per daemon). |
| `relay fingerprint mismatch` | The relay was re-generated but the peers' `relays.yaml` still pins the old fingerprint. Re-run `scripts/smoke_setup.py`. |
| `unknown peer id 'peer-b'` from `sync run_backup` | peer-A's `devices.yaml` is missing the entry — re-run the setup script. |
| Daemon stalls before printing `RelayDaemon up` | Relay isn't up, or its port differs from the value in `relays.yaml`. Start terminal 1 first; the default port is `19000`. |
| Multiple `relay.current` pointer files fighting | Two daemons share an `XDG_RUNTIME_DIR`. Each daemon must export a distinct one. |

## What this smoke does **not** test

* Real-world NAT traversal. Everything is on `127.0.0.1`, so the
  hole-punch burst in `dsync/network/hole_punch.py` is bypassed —
  the daemon just opens a direct QUIC connection to the
  relay-observed endpoint. Cross-WAN testing needs Docker + iptables
  MASQUERADE (PR 8 hardening).
* Daemon long-running stability. No keepalive yet; NAT mappings will
  expire after ~30–60 s of idle in production.
* Windows. IPC is Unix-domain-socket only for now.
