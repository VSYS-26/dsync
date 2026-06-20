# Daemons

`dsync` kann als Hintergrunddienst laufen, der beim Start des Geraets automatisch
hochfaehrt. Die Daemon-Erstellung ist abstrahiert: eine Basisklasse `Daemon`
(`dsync/daemon/base.py`) plus ein geteilter `ServiceInstaller`
(`dsync/daemon/installer.py`) kapseln die OS-spezifische Installation
(systemd / launchd / Windows-Service). Konkrete Daemons liegen in
`dsync/daemon/daemons.py`.

Aktuell gibt es zwei Daemons, die unabhaengig voneinander aktiviert werden koennen:

- `server`: stellt den Sync-Server bereit, damit sich andere Geraete fuer
  Backup/Mirror verbinden koennen.
- `scheduler`: fuehrt Ordner-Syncs automatisch nach einem Cron-Zeitplan aus.

Weitere Daemons lassen sich nach demselben Muster ergaenzen.

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

## Server-Daemon

`--config-dir` ist eine top-level Option und steht VOR dem Befehlsnamen.

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

## Scheduler-Daemon (Sync nach Zeitintervall)

Der `scheduler`-Daemon fuehrt konfigurierte Ordner automatisch nach einem
Cron-Zeitplan aus. Er nutzt exakt denselben Code-Pfad wie ein manueller
`dsync sync run`.

### Zeitplan pro Ordner

Ein Ordner laeuft automatisch, sobald er ein `interval` (Cron-Ausdruck) hat:

```bash
dsync --config-dir <dir> folder add docs /pfad --mode backup-to-peer --interval '*/30 * * * *'
dsync --config-dir <dir> folder mod docs --interval '0 2 * * *'   # Zeitplan aendern
dsync --config-dir <dir> folder mod docs --clear-interval         # Zeitplan entfernen
```

Ohne `interval` wird der Ordner nur manuell synchronisiert. Ungueltige
Cron-Ausdruecke werden beim Anlegen/Aendern abgelehnt.

Verhalten:

- Jeder Ordner hat seinen eigenen Cron-Ausdruck und seinen eigenen
  Lauf-Zeitstempel. Der Scheduler pollt alle 30 s und startet jeden faelligen
  Ordner.
- Verpasste Laeufe werden nachgeholt: war das Geraet zum geplanten Zeitpunkt aus,
  laeuft der Ordner einmalig beim naechsten Tick.
- `backup-from-peer` ist receive-only und wird uebersprungen (mit einmaliger
  Warnung im Log).
- Cron wird in lokaler Zeit ausgewertet.

### Befehle

```bash
dsync --config-dir <dir> scheduler enable --cert cert.pem --key key.pem
dsync --config-dir <dir> scheduler status
dsync --config-dir <dir> scheduler disable
dsync --config-dir <dir> scheduler run --cert cert.pem --key key.pem   # Vordergrund (Debug)
```

`scheduler enable` legt den OS-Dienst `dsync-scheduler` an und schreibt
`scheduler.yaml`. `scheduler run` ist die Daemon-Nutzlast und laeuft im
Vordergrund - praktisch zum Testen ohne System-Installation.

Die CLI liegt unter `dsync/cli/commands/scheduler/`, der Loop in
`dsync/scheduler/runner.py`.

### Logging

Der Scheduler protokolliert jeden Lauf (Start und Ergebnis) nach
`<config-dir>/logs/dsync-scheduler.log` und auf stdout (unter Linux ins Journal):

```
folder=docs running
folder=docs result=success total=1 failed=0 duration=2.3s
```

Erfolgreiche Laeufe werden in `<config-dir>/scheduler-state.json` festgehalten;
fehlgeschlagene bleiben faellig und werden beim naechsten Tick erneut versucht.

### Config-Aenderungen und der Server-Daemon

`folder add/mod/rm` startet den `server`-Daemon neu - aber nur, wenn er vorher
aktiviert (und laufend) war -, damit neue Ordner/Geraete sofort greifen. Der
Scheduler braucht keinen Neustart: er laedt die Config bei jedem Tick neu.

---

## Pro Betriebssystem

### Linux (systemd)

- Units: `/etc/systemd/system/dsync-server.service` und
  `/etc/systemd/system/dsync-scheduler.service`, Autostart via
  `WantedBy=multi-user.target`.
- `enable`/`disable` rufen intern `sudo systemctl ...` (Passwort-Abfrage).
- Logs landen im Journal:

```bash
journalctl -u dsync-server --no-pager | tail
journalctl -u dsync-scheduler --no-pager | tail
```

### macOS (launchd)

- Agents: `~/Library/LaunchAgents/dsync-server.plist` und
  `~/Library/LaunchAgents/dsync-scheduler.plist` (`RunAtLoad`).
- Logs unter `<config-dir>/logs/`.

### Windows

- Windows-Dienste via `pywin32`; `enable`/`disable` in einer
  Administrator-Konsole ausfuehren.

```powershell
sc query dsync-server
sc query dsync-scheduler
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

`scheduler enable` schreibt `scheduler.yaml` (`dsync/config/scheduler.py`); beide
Dateien koexistieren:

```yaml
enabled: true
cert: cert.pem
key: key.pem
```

---

## Entfernen

`dsync --config-dir <dir> server disable` bzw.
`dsync --config-dir <dir> scheduler disable` stoppt den jeweiligen Dienst, entfernt
den OS-Dienst und den Boot-Autostart. Bei einer Bundle-Installation wird
anschliessend das Bundle selbst entfernt (siehe [Installation](installation.md)).
