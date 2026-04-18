# Protokoll – 18.04.2026

## Ticket-Management

Vorgestellte Tickets werden künftig mit ihren Ergebnissen primär in der Kommentarspalte dokumentiert. Jira-Tickets sollen konsequent zugewiesen und mit dem korrekten Status versehen werden – sobald ein Ticket aktiv bearbeitet wird, ist der Status auf **„In Bearbeitung"** zu setzen.

Jeder erstellt auf Basis seiner eigenen Tickets entsprechende **Subtickets**, die dann konkret bearbeitet werden können.

## Dokumentation & Architektur

Janis richtet **GitHub Pages** für Recherche und Dokumentation ein. Zur Dokumentation von Architekturentscheidungen wird das **ADR-Format** (Architectural Decision Record) genutzt. Als Konfigurationsformat wurde **YAML** festgelegt.

## Entwicklungsrichtlinien

Zu entwickelnde Blöcke und Komponenten sollen so **modular wie möglich** gehalten werden, um gegenseitige Abhängigkeiten in der Entwicklung zu minimieren und Konflikte zu vermeiden.

### Branch-Strategie

Es gibt zwei Hauptbranches:

- **`main`** – Produktions-Branch, auf den ausgeliefert wird
- **`dev`** – Entwicklungs-Branch, auf den per Squashed PR gemergt wird

Feature-Branches werden per **Rebase** auf den aktuellen Stand gebracht.

### Branch-Benennung

Branches folgen dem Schema `<typ>/ticket-suffix`, z. B. `feat/VSYS26T2-38`. Folgende Typen sind definiert:

| Präfix | Verwendung |
|---|---|
| `feat/` | Neue Features |
| `fix/` | Fehlerbehebungen |
| `refactor/` | Code-Umstrukturierungen |
| `test/` | Reine Testfälle, die noch nicht im Feature-Branch behandelt wurden |
| `chore/` | Alles, was keiner der obigen Kategorien zuzuordnen ist |

Als Referenz dienen die **Conventional Commits**: https://www.conventionalcommits.org/en/v1.0.0/

## Meetings

Regelmäßige Treffen finden **dienstags um 11:30 Uhr** statt – jeweils vor der Übungsstunde. Der erste Termin wird abgewartet, um zu sehen, was in der Übung erarbeitet wird, bevor weitere Schritte festgelegt werden.