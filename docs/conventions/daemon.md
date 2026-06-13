# Daemons

`dsync` kann als Hintergrunddienst laufen, der beim Start des Geraets automatisch
hochfaehrt. Die Daemon-Erstellung ist abstrahiert: eine Basisklasse `Daemon`
(`dsync/daemon/base.py`) plus ein geteilter `ServiceInstaller`
(`dsync/daemon/installer.py`) kapseln die OS-spezifische Installation
(systemd / launchd / Windows-Service). Konkrete Daemons liegen in
`dsync/daemon/daemons.py`.

Aktuell gibt es den `server`-Daemon: er stellt den Sync-Server bereit, damit sich
andere Geraete fuer Backup/Mirror verbinden koennen. Weitere Daemons lassen sich
nach demselben Muster ergaenzen.

---

## Voraussetzung

`dsync` muss installiert sein und die ausfuehrbare Binary muss fuer systemd
erreichbar sein. Praktisch heisst das: system-weit installieren (nach `/usr`) oder
das Release-Bundle nach `/opt` legen und auf Fedora mit `chcon -R -t bin_t`
freigeben. Details in [Installation](installation.md).

Hintergrund: systemd darf eine Binary unter `/home` oder `/tmp` nicht ausfuehren
(`status=203/EXEC`); nur Pfade mit SELinux-Typ `bin_t` (z. B. unter `/usr`)
funktionieren.

---

## Zertifikate

Der Server braucht `cert.pem` und `key.pem` im Config-Verzeichnis. Erzeugen mit:

```bash
python -m dsync.crypto.setup_certs     # schreibt cert.pem/key.pem ins aktuelle Verzeichnis
```

Anschliessend in das gewuenschte Config-Verzeichnis legen.

---

## Befehle

`--config-dir` ist eine top-level Option und steht VOR `server`.

```bash
dsync --config-dir <dir> server enable --port 9999 --cert cert.pem --key key.pem
dsync --config-dir <dir> server status
dsync --config-dir <dir> server disable
```

`server enable` legt den OS-Dienst an, aktiviert den Autostart beim Boot, startet
ihn und schreibt `daemon.yaml` in das Config-Verzeichnis. Optionen-Defaults:
`--port 9999`, `--cert cert.pem`, `--key key.pem`.

Die CLI liegt unter `dsync/cli/commands/server/`, die geteilte enable/disable/
status-Logik in `dsync/cli/daemon_ops.py`.

---

## Pro Betriebssystem

### Linux (systemd)

- Unit: `/etc/systemd/system/dsync-server.service`, Autostart via
  `WantedBy=multi-user.target`.
- `enable`/`disable` rufen intern `sudo systemctl ...` (Passwort-Abfrage).
- Logs landen im Journal:

```bash
journalctl -u dsync-server --no-pager | tail
```

### macOS (launchd)

- Agent: `~/Library/LaunchAgents/dsync-server.plist` (`RunAtLoad`).
- Logs unter `<config-dir>/logs/`.

### Windows

- Windows-Dienst via `pywin32`; `server enable`/`disable` in einer
  Administrator-Konsole ausfuehren.

```powershell
sc query dsync-server
```

---

## Konfiguration

`server enable` schreibt `daemon.yaml` ins Config-Verzeichnis
(`dsync/config/daemon.py`):

```yaml
enabled: true
port: 9999
cert: cert.pem
key: key.pem
```

---

## Entfernen

`dsync --config-dir <dir> server disable` stoppt den Dienst, entfernt den OS-Dienst
und den Boot-Autostart. Bei einer Bundle-Installation wird anschliessend das Bundle
selbst entfernt (siehe [Installation](installation.md)).
