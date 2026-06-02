# Tests

## Übersicht

Die Testsuite von `dsync` ist in vier Schichten gegliedert. Jede Schicht testet eine andere Abstraktionsebene – von isolierter Einzellogik bis hin zu vollständigen Sync-Abläufen über echte Netzwerkverbindungen.

```
tests/
├── conftest.py                    # Gemeinsame Fixtures
├── unit/                          # Isolierte Einheitentests
│   ├── config/                    # Konfigurations-Modelle
│   ├── crypto/                    # Kryptographie-Funktionen
│   ├── network/                   # Protokoll-Framing, Dateiübertragung, Validierung
│   ├── test_identity.py           # PeerMapStore
│   └── test_integrity.py          # SHA-256-Hashing
└── integration/                   # Integrationstests
    ├── cli/                       # CLI-Befehle via CliRunner
    └── sync/                      # Protokoll-Austausch über In-Process-Streams
```

---

## Schichten

### Unit-Tests (`tests/unit/`)

Testen einzelne Klassen und Funktionen **vollständig isoliert** – kein Netzwerk, kein echtes Dateisystem außer `tmp_path`, kein Keystore.

| Modul | Was getestet wird |
|---|---|
| `config/test_devices_config.py` | YAML laden/speichern, Pydantic-Validierung, Fingerprint-Formate, Duplikate |
| `config/test_folders_config.py` | YAML laden/speichern, alle `SyncMode`-Werte, `devices`/`recursive`-Defaults |
| `crypto/test_keys.py` | Ed25519-Keypair-Generierung, Fingerprint-Formate, `is_valid_fingerprint`, PEM-Round-Trip, Keystore-Wrapper-Funktionen |
| `crypto/test_setup_certs.py` | X.509-Zertifikat-Generierung, Selbstsignierung, Gültigkeitsdauer, RSA-2048 |
| `crypto/test_secure_storage.py` | `SecureKeyStorage` mit In-Memory-Keyring-Mock, store/retrieve/delete/has_keypair, `KeyringError`-Propagierung |
| `network/test_p2p_core.py` | Length-Prefix-Framing, AUTH-Frame-Validierung, CONFIG-Größenlimit, ACK-Austausch, Mid-Stream-EOF-Fehler |
| `network/test_file_transfer.py` | Dateiübertragung (klein/groß/leer), Verzeichnisstruktur, SHA-256-Prüfung, Path-Traversal-Abwehr, Frame-Validierungsfehler |
| `network/test_config_validation.py` | Komplementäre Modi, Device-Whitelist, `recursive`-Übereinstimmung |
| `network/test_backup_direction.py` | `BackupSession`-Richtungsdurchsetzung, `DirectionViolationError` |
| `network/test_node_statics.py` | `P2PNode`-Statik-Methoden: Auth-Packing, RSA-PSS-Signaturprüfung |
| `test_identity.py` | TTL-Ablauf, Upsert, Purge, JSON-Persistenz, fehlerhafte JSON-Einträge (fehlende Felder, ungültige IP, falscher Typ), `peer_map_path` |
| `test_integrity.py` | SHA-256-Berechnung, leere Dateien, große Dateien |

### Integrationstests (`tests/integration/`)

Testen das **Zusammenspiel mehrerer Komponenten**. Netzwerkverbindungen laufen über In-Process-Socket-Paare (`socketpair()`), kein echtes Netzwerk nötig.

| Modul | Was getestet wird |
|---|---|
| `cli/test_device_commands.py` | `device add/rm/mod/list` via Typer `CliRunner`, YAML-Persistenz, alle Fehlerpfade, Duplikat-Fingerprint bei `mod` |
| `cli/test_folder_commands.py` | `folder add/rm/mod/list` via Typer `CliRunner`, Device-Referenzvalidierung, fehlende Config-Verzeichnisse |
| `cli/test_peer_commands.py` | `peer announce/discover/map` mit gemockten mDNS-Komponenten und Keyring, KeyboardInterrupt-Handling |
| `cli/test_sync_commands.py` | `sync start/run` mit gemocktem `P2PNode`, Fehlerpfade (kein Peer im Map, ConnectionRefused) |
| `sync/test_config_exchange.py` | `CONFIG`/`CONFIG_ACK`-Austausch über verbundene Streams auf Protokollebene |
| `sync/test_config_exchange_class.py` | `ConfigExchange`-Klasse: paralleler Source/Peer-Ablauf, `FoldersConfig`-Round-Trip |
| `sync/test_file_sync_loopback.py` | Vollständiger `BackupSession`-Ablauf (Source → Peer), mehrere Dateien, Verzeichnisstruktur, SHA-256-Integrität |

---

## Tests ausführen

**Alle Tests:**

```bash
uv run pytest tests/
```

**Nur Unit-Tests (schnell, kein I/O):**

```bash
uv run pytest tests/unit/
```

**Nur Integrationstests:**

```bash
uv run pytest tests/integration/
```

**Ohne E2E-Tests (Standard für CI):**

```bash
uv run pytest -m "not e2e"
```

**Mit Coverage-Report:**

```bash
uv run pytest tests/ --cov=dsync --cov-report=term-missing
```

**Einzelne Datei oder Test:**

```bash
uv run pytest tests/unit/network/test_file_transfer.py
uv run pytest tests/unit/network/test_file_transfer.py::test_path_traversal_rejected
```

---

## Abhängigkeiten

Die Test-Abhängigkeiten sind als eigene Gruppe in `pyproject.toml` definiert und werden mit `uv sync --group test` installiert (läuft automatisch mit `uv sync`):

```toml
[dependency-groups]
test = [
    "pytest>=8",
    "pytest-asyncio>=0.25",
    "pytest-cov>=6",
]
```

**pytest-asyncio** ist notwendig, weil große Teile des Netzwerk-Codes async sind. Der Modus `asyncio_mode = "auto"` in `pyproject.toml` sorgt dafür, dass async Testfunktionen automatisch als asyncio-Tests behandelt werden – kein `@pytest.mark.asyncio`-Dekorator nötig.

### Coverage-Schwellenwert

Der Mindestwert für Coverage ist auf **70 %** gesetzt (`--cov-fail-under=70` in `addopts`). Folgende Dateien sind von der Messung ausgenommen, da sie Einstiegspunkte oder generierte Hilfsskripte sind:

```toml
[tool.coverage.run]
omit = [
    "dsync/main.py",
    "dsync/generate_test_certs.py",
    "dsync/network/_start_peer.py",
]
```

### Warnungen unterdrücken

`SecureKeyStorage` gibt bei erzwungenem Überschreiben von Keys eine `UserWarning` aus. Da das in Tests absichtlich passiert, wird diese Warnung global unterdrückt:

```toml
[tool.pytest.ini_options]
filterwarnings = [
    "ignore:Overwriting existing.*key.*:UserWarning",
]
```

---

## Konventionen

### Dateinamen

Testdateien beginnen immer mit `test_`, gefolgt vom Namen des zu testenden Moduls:

```
dsync/network/file_transfer.py  →  tests/unit/network/test_file_transfer.py
dsync/config/device.py          →  tests/unit/config/test_devices_config.py
```

### Testnamen

Testnamen beschreiben das **beobachtbare Verhalten**, nicht die interne Implementierung. Schema: `test_<was>_<erwartetes Ergebnis>`:

```python
def test_duplicate_id_raises() -> None: ...
def test_path_traversal_rejected() -> None: ...
def test_peer_can_receive_from_empty_stream() -> None: ...
```

### Fixtures

Gemeinsame Fixtures liegen in `tests/conftest.py`:

| Fixture | Scope | Beschreibung |
|---|---|---|
| `tmp_config_dir` | function | `tmp_path` mit leeren `devices.yaml` und `folders.yaml` |
| `valid_fp` | function | Gültiger Hex-Fingerprint (`hex-` + 64 `a`s) |
| `cli_runner` | function | `typer.testing.CliRunner`-Instanz |
| `stream_pair` | function | Zwei verbundene asyncio-Stream-Paare über `socketpair()` |

Async Fixtures werden mit `@pytest_asyncio.fixture` deklariert.

Fixtures mit engem Scope (nur ein Test) werden direkt im Testfile definiert, nicht in `conftest.py`.

### Async Tests

Async Testfunktionen werden direkt als `async def` geschrieben:

```python
async def test_send_recv_roundtrip(stream_pair) -> None:
    (reader_a, writer_a), (reader_b, writer_b) = stream_pair
    await async_send_msg(writer_a, MsgType.HELLO, b"payload")
    msg_type, data = await async_recv_msg(reader_b)
    assert msg_type == MsgType.HELLO
```

### Stream-Paare für Netzwerktests

Netzwerktests nutzen `socketpair()` um zwei verbundene asyncio-Streams in-process zu erstellen – kein echter Server, kein Port-Binding:

```python
sock_a, sock_b = socket.socketpair()
reader_a, writer_a = await asyncio.open_connection(sock=sock_a)
reader_b, writer_b = await asyncio.open_connection(sock=sock_b)
```

Alles, was auf der `writer_a`-Seite gesendet wird, landet auf `reader_b` – und umgekehrt.

### CLI-Tests

CLI-Befehle werden über Typers `CliRunner` getestet. Das `--config-dir`-Flag wird immer explizit auf ein temporäres Verzeichnis gesetzt:

```python
def test_device_add_success(cli_runner: CliRunner, tmp_config_dir: Path) -> None:
    result = runner.invoke(cli, ["--config-dir", str(tmp_config_dir), "device", "add", "dev-a", _FP_A])
    assert result.exit_code == 0
    devices = DevicesConfig.load(tmp_config_dir)
    assert any(d.id == "dev-a" for d in devices.trusted_devices)
```

Nach dem Aufruf wird das **tatsächlich geschriebene YAML** über das Modell neu geladen und geprüft – nicht nur der Ausgabe-Text. Das stellt sicher, dass der Persistenzpfad vollständig durchlaufen wurde.

---

## Marker

Spezielle Marker können an Tests annotiert werden:

| Marker | Bedeutung |
|---|---|
| `@pytest.mark.e2e` | End-to-End-Test, der echte Subprozesse startet (langsam) |
| `@pytest.mark.slow` | Test dauert mehr als 1 Sekunde |

```bash
# Nur E2E-Tests ausführen
uv run pytest tests/ -m e2e

# E2E-Tests überspringen
uv run pytest tests/ -m "not e2e"
```

---

## CI

GitHub Actions führt die Pipeline bei jedem Push und bei Pull Requests auf `main` oder `dev` aus (`.github/workflows/test.yml`).

| Schritt | Befehl | Beschreibung |
|---|---|---|
| Type check | `uv run mypy dsync` | Statische Typprüfung mit mypy (strict mode) |
| Tests | `uv run pytest -m "not e2e"` | Unit- und Integrationstests ohne E2E |
| Coverage-Report | — | Wird als Artefakt (`coverage.xml`) gespeichert |

E2E-Tests werden in CI übersprungen — sie starten echte Subprozesse und sind für eine automatisierte Pipeline zu langsam.