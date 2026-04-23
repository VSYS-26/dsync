# CLI-Commands

CLI basiert auf **[Typer](https://github.com/fastapi/typer)** + **[Rich](https://github.com/textualize/rich)**

Jedes Command oder jede Gruppe liegt in einer eigenen Datei unter `dsync/cli/commands/`

Registriert werden alle Commands zentral in `dsync/cli/__init__.py`

---

## Dateinamen

Echte Commands: `<command_name>.py` bzw. `<command_name>/` (Dateiname = CLI-Command, z.B. `init.py` → `dsync init`)

Platzhalter-Beispiele: `_<command_name>` mit Unterstrich-Prefix

---

## Muster

### Einfaches Command

Plain Function exportieren, in `dsync/cli/__init__.py` via `cli.command()(<fn>)` registrieren

Vorlage: `dsync/cli/commands/_hello.py`

### Command-Gruppe

Pro Gruppe ein eigener Ordner `dsync/cli/commands/<group>/`. Ein File pro Sub-Befehl nach [Typer one-file-per-command](https://typer.tiangolo.com/tutorial/one-file-per-command/)

Jedes Sub-Befehl-File exportiert `app: typer.Typer = typer.Typer()` mit genau einem `@app.command()`-Decorator

Die `__init__.py` der Gruppe definiert die Gruppen-`app` und Sub-Befehle via `app.add_typer(<sub>.app)`. `no_args_is_help=True` auf der Gruppe setzen (zeigt Hilfe, wenn die Gruppe ohne Sub-Befehl aufgerufen wird)

In `dsync/cli/__init__.py` via `cli.add_typer(<group>.app, name="...")` registrieren

Vorlage: `dsync/cli/commands/_demo/`

---

## Argumente und Options

Annotated-Stil bei Optionen
```python
name: Annotated[str, typer.Argument(help="Name to greet")] = "world"
flag: Annotated[bool, typer.Option("--flag", "-f", help="Enable flag")] = False
```
Default-Wert rechts vom `=`, nicht als erstes Argument in `typer.Argument(...)`

Bei Pflicht-Argument: kein Default setzen

Alle Parameter + Return-Type (`-> None`) annotiert (für automatische Type-Checks)

---

## Docstrings und Help-Texte

Jede Command-Funktion bekommt einen Docstring. Typer nutzt sie automatisch als Command-Help
```python
def hello(
    name: Annotated[str, typer.Argument(help="Name to greet")] = "world",
) -> None:
    """Greet someone by name."""
    success(f"Hello, {name}!")
```
Erste Zeile prägnanter Satz (`Greet someone by name.`)

Bei längerer Hilfe: Leerzeile, danach Details

Argument-spezifische Hilfe bleibt im `typer.Argument(help="...")` / `typer.Option(help="...")`

---

## Rich-Output

Ausschließlich die geteilte Console aus `dsync.cli.console` nutzen

keine eigenen `Console()`-Instanzen
```python
from dsync.cli.console import console, success, warn, error, info

console.print(f"[info]would add:[/info] {item_id}")   # Inline-Markup
success("Sync completed.")                            # Helper für einfache Nachrichten
```
Verfügbare Styles (definiert in `dsync/cli/console.py`): `info`, `success`, `warn`, `error`. Neue Styles zentral im Theme ergänzen, nicht ad-hoc in einzelnen Commands

---

## App-State

Beim Start lädt `dsync/cli/commands/config_dir.py` die YAML-Configs aus dem per `--config-dir` / `-c` übergebenen Ordner (Default: `./dsync-config/`) und legt sie als `AppState` (`dsync/state.py`) auf `ctx.obj` ab.

Zugriff aus einem Command:
```python
import typer
from dsync.state import AppState

def my_command(ctx: typer.Context) -> None:
    """Example command using app state."""
    state: AppState = ctx.obj
    # state.folders / state.devices / state.config_dir
```

Fehlende Config-Dateien oder ein nicht existierendes Verzeichnis sind kein Fehler – die Configs sind dann schlicht leer. Ein neues Verzeichnis wird erst beim Save angelegt.

---

## Tests

#TODO Test-Strategie für CLI-Commands definieren nachdem generelle Teststrategie festgelegt wurde
