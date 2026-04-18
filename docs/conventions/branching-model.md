# Branching

## Übersicht

Das Projekt verwendet ein schlankes, auf **Squash-Merges** basierendes Branching-Modell mit zwei dauerhaften Branches. Ziel ist es, eine saubere und lineare Git-Historie zu gewährleisten und gleichzeitig die parallele Entwicklung mehrerer Features zu ermöglichen.

---

## Dauerhafte Branches

### `main`
Der Produktions-Branch. Enthält ausschließlich stabilen, ausgelieferten Code. Direkte Commits auf `main` sind **nicht erlaubt** – Änderungen gelangen ausschließlich über `dev` per Release-Merge hierher.

### `dev`
Der zentrale Entwicklungs-Branch. Alle abgeschlossenen Feature-, Fix- und Refactor-Branches werden hier per **Squashed Pull Request** zusammengeführt. `dev` ist die Basis für alle neuen Feature-Branches.

---

## Feature-Branches

Jeder neue Feature-, Fix- oder sonstige Entwicklungs-Branch wird von `dev` abgezweigt und nach Abschluss der Arbeit wieder per Squashed PR in `dev` zurückgeführt.

```
dev
 ├── feat/VSYS26T2-38
 ├── fix/VSYS26T2-12
 └── refactor/VSYS26T2-21
```

---

## Branch-Benennung

Branches folgen dem Schema:

```
<typ>/<ticket-id>
```

**Beispiel:** `feat/VSYS26T2-38`

Die Ticket-ID entspricht dem zugehörigen Jira-Ticket und stellt die direkte Rückverfolgbarkeit sicher.

### Erlaubte Typen

| Typ | Verwendung |
|---|---|
| `feat/` | Implementierung eines neuen Features |
| `fix/` | Behebung eines Fehlers oder Bugs |
| `refactor/` | Umstrukturierung von Code ohne funktionale Änderung |
| `test/` | Reine Testfälle, die noch nicht im zugehörigen Feature-Branch behandelt wurden |
| `chore/` | Alles, was keiner der obigen Kategorien zuzuordnen ist (z. B. Konfiguration, Dokumentation, Abhängigkeiten) |

---

## Rebase statt Merge

Um eine saubere, lineare Historie auf Feature-Branches zu behalten, wird **Rebase** anstelle von Merge verwendet, wenn ein Feature-Branch auf den aktuellen Stand von `dev` gebracht werden muss.

```bash
# Feature-Branch auf aktuellen dev-Stand bringen
git fetch origin
git rebase origin/dev
```

!!! warning "Achtung bei gemeinsam genutzten Branches"
    Rebase schreibt die Commit-Historie um. Bei Branches, auf denen mehrere Personen arbeiten, muss dies vorab abgesprochen werden.

---

## Pull Request Workflow

1. Feature-Branch von `dev` erstellen
2. Entwicklung durchführen, regelmäßig auf `dev` rebasen
3. Pull Request gegen `dev` öffnen
4. Code Review abwarten und Feedback einarbeiten
5. Merge als **Squash Commit** durchführen
6. Feature-Branch nach dem Merge löschen

---

## Commit-Konventionen

Commit-Messages orientieren sich an den **Conventional Commits**:

```
<typ>(<scope>): <kurze Beschreibung>

[optionaler Body]

[optionaler Footer, z. B. Jira-Ticket-Referenz]
```

**Beispiele:**

```
feat(auth): Login-Seite implementieren

fix(api): Fehler bei leerem Response-Body behoben

Refs: VSYS26T2-38
```

Weitere Details: [conventionalcommits.org](https://www.conventionalcommits.org/en/v1.0.0/)