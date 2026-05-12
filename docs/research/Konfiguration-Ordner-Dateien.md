# Konfigurationsspezifikation – Ordner & Dateien

Dieses Dokument beschreibt, wie Ordner und Dateien in dsync konfiguriert werden.

---

## Überblick

Ein Nutzer kann sowohl **ganze Ordner** als auch **einzelne Dateien** zur Synchronisierung hinzufügen. Jeder Eintrag bekommt einen eindeutigen Namen, einen Pfad und einen Sync-Modus.

---

## Sync-Modi

| Modus | Beschreibung                                                                                                                        |
|---|-------------------------------------------------------------------------------------------------------------------------------------|
| `mirror` | Beide Seiten werden auf denselben Stand gebracht. Änderungen auf einem Gerät werden auf das andere übertragen – in beide Richtungen. |
| `backup-to-peer` | Nur das lokale Gerät überträgt Daten an den Peer. Der Peer lädt nichts zurück. Geeignet als einseitiges Backup.                     |
| `backup-from-peer` | Nur der Peer überträgt Daten zum lokalen Gerät. Das lokale Gerät lädt nichts zurück. Gegenstück zu `backup-to-peer`.                  |
---

## Konfiguration eines Ordners

Ein Ordner-Eintrag besteht aus:

| Feld | Beschreibung                                                                                                                                                                                                                  | Pflicht |
|---|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---|
| `id` | Eindeutiger Name für diesen Eintrag                                                                                                                                                                                           | ✅ |
| `path` | Pfad zum Ordner auf dem lokalen Gerät                                                                                                                                                                                         | ✅ |
| `mode` | Sync-Modus (`mirror`, `backup-to-peer` oder `backup-from-peer`)                                                                                                                                                                | ✅ |
| `devices` | Liste von Geräten, die diesen Ordner synchronisieren sollen (z. B. `["laptop", "desktop"]`). Angegeben werden dabei die device-ids. Falls das Feld weggelassen wird, werden alle trusted devices aus `devices.yaml` verwendet. | ❌ |
**Beispiel:**

```yaml
id: dokumente
path: /home/user/Dokumente
mode: mirror
devices: 
    - laptop
    - desktop
```

---

## Konfiguration einer einzelnen Datei

Eine einzelne Datei wird genauso konfiguriert wie ein Ordner – nur dass der Pfad auf eine Datei zeigt:

| Feld | Beschreibung                                                                                                                                                                                                                 | Pflicht |
|---|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---|
| `id` | Eindeutiger Name für diesen Eintrag                                                                                                                                                                                          | ✅ |
| `path` | Pfad zu der Datei auf dem lokalen Gerät                                                                                                                                                                                      | ✅ |
| `mode` | Sync-Modus (`mirror`, `backup-to-peer` oder `backup-from-peer`)                                                                                                                                                              | ✅ |
| `devices` | Liste von Geräten, die diese Datei synchronisieren sollen (z. B. `["laptop", "desktop"]`). Angegeben werden dabei die device-ids. Falls das Feld weggelassen wird, werden alle trusted devices aus `devices.yaml` verwendet. | ❌ |

**Beispiel:**

```yaml
id: notizen
path: /home/user/notizen.txt
mode: backup-to-peer
```

---

## Gesamte Konfigurationsdatei

Alle Einträge werden gemeinsam in einer Konfigurationsdatei gespeichert:

```yaml
entries:
  - id: dokumente
    path: /home/user/Dokumente
    mode: mirror
    devices: 
      - laptop
      - desktop

  - id: notizen
    path: /home/user/notizen.txt
    mode: backup-to-peer
```

---

## Offene Punkte

- Sollen bestimmte Dateien oder Dateitypen ausgeschlossen werden können (z. B. `.tmp`, `.log`)?

---

## Regeln

- Jede `id` muss eindeutig sein – zwei Einträge dürfen nicht denselben Namen haben.
- Der angegebene `path` muss auf dem lokalen Gerät existieren.
- Der `mode` darf nur `mirror`, `backup-to-peer` oder `backup-from-peer` sein.
- Die `devices`-Liste darf nur gültige device-ids enthalten, die in `devices.yaml` definiert sind oder ansonsten weggelassen werden, um alle trusted devices zu verwenden.