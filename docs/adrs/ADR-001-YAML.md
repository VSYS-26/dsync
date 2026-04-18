# ADR-001: YAML für Konfiguration

- **Datum:** 2026-04-18
- **Status:** `Akzeptiert`
- **Erstellt von:** Janis E.

---

## Kontext

>Die Anwendung benötigt eine flexible und leicht anpassbare Konfiguration, die sowohl von Entwickler:innen als auch von technisch weniger versierten Nutzer:innen verstanden und bearbeitet werden kann.
>
> Ziel ist es, ein Format zu wählen, das:
>
> * gut lesbar ist
> * einfach erweitert werden kann

## Entscheidung

> Für die Konfiguration der Anwendung wird YAML als Standardformat verwendet.

## Begründung

> YAML bietet eine sehr gute Balance zwischen Lesbarkeit und Ausdrucksstärke. Durch die reduzierte Syntax und die Möglichkeit, verschachtelte Strukturen übersichtlich darzustellen, eignet es sich besonders gut für Konfigurationsdateien.
> 
> Im Vergleich zu anderen Formaten ist YAML weniger "visuell laut" (keine vielen Klammern oder Anführungszeichen) und dadurch einfacher zu erfassen - gerade bei komplexeren Konfigurationen.
> 
> Zusätzlich wird YAML von vielen Tools im Ökosystem bereits unterstützt (z.B. CI/CD-Pipelines wie GitHub Actions oder Konfigurationssysteme wie Kubernetes), was die Integration erleichtert und den Lernaufwand reduziert.

### Betrachtete Alternativen

| Alternative | Begründung für Ablehnung                                                                                                             |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| JSON        | Strengere Syntax (z.B. Pflicht für Anführungszeichen, keine Kommentare), dadurch weniger benutzerfreundlich für manuelle Bearbeitung |
| TOML        | Zwar lesbar, aber weniger verbreitet im bestehenden Tooling und weniger flexibel bei komplex verschachtelten Strukturen              |
| XML         | Sehr verbose und schwer lesbar, ungeeignet für einfache Konfigurationszwecke                                                         |

## Konsequenzen

### Positiv

- Gute Lesbarkeit auch für größere Konfigurationsdateien
- Einfache manuelle Bearbeitung ohne spezielle Tools
- Unterstützung von Kommentaren direkt in der Datei
- Weit verbreitet in modernen DevOps- und Infrastruktur-Tools
- Gut geeignet für verschachtelte und strukturierte Daten

### Negativ / Trade-offs

- Einrückungsbasierte Syntax kann zu Fehlern führen (Whitespace-sensitiv)
- Parsing kann komplexer sein als bei einfacheren Formaten wie JSON
- Unterschiedliche YAML-Versionen können zu Inkonsistenzen führen
- Weniger strikt → Fehler werden teilweise erst zur Laufzeit erkannt

---

## Referenzen

* [Research Ticket](https://kp2-jira.in.htwg-konstanz.de/browse/VSYS26T2-29)
