# Setup

Diese Anleitung beschreibt die lokale Einrichtung von `dsync` fuer die Entwicklung.

## Voraussetzungen

- Installiertes `uv`
- Git
- VS Code (optional, empfohlen)

## Projekt einrichten

```bash
git clone <repo-url>
cd dsync
uv sync
```

## Qualitaets-Checks aktivieren

Die Hooks werden bei jedem Commit automatisch ausgefuehrt.

```bash
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg
```

## Checks manuell ausfuehren

```bash
uv run pre-commit run --all-files
```

## Wichtige Tools im Projekt

- `Ruff`: Linting, Formatting, Import-Sortierung, Auto-Fixes, Docstring-Checks
- `mypy`: Statische Typpruefung (strict)
- `Bandit`: Security-Checks
- `commit-msg` Hook: Prueft Conventional Commits

## VS Code (empfohlen)

Installiere die empfohlenen Erweiterungen aus `.vscode/extensions.json`.
Beim Speichern werden Formatierung, Auto-Fixes und Import-Sortierung mit Ruff ausgefuehrt.

## Beispiel fuer gueltige Commit-Message

```text
feat(sync): add peer discovery
```

