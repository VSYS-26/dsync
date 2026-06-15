# Installation

Diese Anleitung beschreibt die lokale Installation von `dsync` als fertige
Anwendung (CLI-Befehl `dsync`). Fuer die Entwicklungsumgebung siehe
[Setup](setup.md), fuer den Hintergrundbetrieb siehe [Daemons](daemon.md).

---

## Variante A - Aus dem Quellcode

### Entwicklung / editierbar

```bash
git clone <repo-url>
cd dsync
uv sync
uv run dsync --help
```

### Reine CLI-Nutzung

Installiert den Befehl `dsync` isoliert nach `~/.local/bin`:

```bash
uv tool install .
# oder
pipx install .
```

Hinweis: `~/.local` ist NICHT fuer den Daemon geeignet (siehe SELinux-Hinweis
unten). Fuer den Hintergrunddienst system-weit installieren.

### System-weit (Voraussetzung fuer den Daemon)

```bash
sudo /usr/bin/python3 -m pip install --break-system-packages .
command -v dsync          # -> /usr/local/bin/dsync
```

Die Binary landet unter `/usr/local/bin` und das Modul unter `/usr/local/lib`,
beides fuer systemd zugaenglich.

---

## Variante B - Aus dem GitHub-Release

Releases werden automatisch gebaut: ein Git-Tag `v*` startet
`.github/workflows/release.yml`, das je Betriebssystem ein vorgebautes
PyInstaller-Bundle (onedir) erzeugt. Es ist kein Python auf dem Zielsystem noetig.

Assets (Namensschema `dsync-<os>-<arch>-<kurzhash>.<ext>`, z. B.
`dsync-linux-x64-ab12cd3.tar.gz`):

- `dsync-linux-x64-<hash>.tar.gz`
- `dsync-macos-arm64-<hash>.tar.gz`
- `dsync-windows-x64-<hash>.zip`

### Bundle lokal bauen (Alternative zum Download)

Statt das Release-Asset zu laden, kann dasselbe onedir-Bundle lokal gebaut werden
(benoetigt `uv`):

```bash
uv sync --group dev
uv run pyinstaller packaging/dsync.spec     # -> dist/dsync/
```

Danach `dist/dsync/` genauso installieren wie ein heruntergeladenes Bundle (Schritte
unten; als Quelle dann `dist/dsync` statt des entpackten `dsync/`).

### Linux

```bash
tar -xzf dsync-linux-x64-*.tar.gz                # ergibt ./dsync/
sudo cp -r dsync /opt/dsync
sudo ln -s /opt/dsync/dsync /usr/local/bin/dsync
sudo chcon -R -t bin_t /opt/dsync                # nur Fedora/SELinux
dsync --help
```

### macOS

```bash
tar -xzf dsync-macos-arm64-*.tar.gz
sudo cp -r dsync /opt/dsync
sudo ln -s /opt/dsync/dsync /usr/local/bin/dsync
xattr -dr com.apple.quarantine /opt/dsync        # falls Gatekeeper blockt
dsync --help
```

### Windows

```powershell
# dsync-windows-x64-<hash>.zip entpacken, z. B. nach C:\Program Files\dsync
# den Ordner zum PATH hinzufuegen, dann:
dsync --help
```

### Ein Release ausloesen

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

---

## SELinux-Hinweis (Fedora)

Damit der Daemon (siehe [Daemons](daemon.md)) startet, muss die ausfuehrbare
Binary fuer systemd zugaenglich sein (SELinux-Typ `bin_t`). Das ist unter `/usr`
gegeben (Variante A system-weit, oder Bundle unter `/opt` mit
`chcon -R -t bin_t`). Eine Binary unter `/home` oder `/tmp` wird von systemd
abgewiesen (`status=203/EXEC`).
