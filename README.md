# Stundenplan-Watcher — BSZ Görlitz → Telegram

Prüft den Vertretungsplan der Klasse **FOS25W** alle 20 Minuten und schickt eine
Telegram-Nachricht, sobald sich etwas ändert. Läuft in GitHub Actions, also
unabhängig vom eigenen Rechner.

## Wie es funktioniert

Der Plan ist ein **Indiware-mobil**-Export unter
`https://www.bszgoerlitz.de/vertret/mobil/` hinter HTTP-Basic-Auth. Dahinter
liegen saubere XML-Dateien — es muss also kein HTML gescrapt werden:

| Datei | Inhalt |
|---|---|
| `mobdaten/Klassen.xml` | der aktuellste Tagesplan + Liste aller schulfreien Tage |
| `mobdaten/PlanKl<JJJJMMTT>.xml` | Tagesplan für ein bestimmtes Datum |

`watcher.py` holt `Klassen.xml`, überspringt anhand der `FreieTage`-Liste alle
Ferien-, Feiertags- und Wochenendtermine und probiert die nächsten 14 Tage
durch. Aus jedem Plan werden der `<Kl>`-Block der eigenen Klasse und die
`<ZusatzInfo>`-Zeilen (schulweite Hinweise) gelesen, mit `state/last.json`
verglichen und nur echte Unterschiede gemeldet.

Der Zeitstempel des Exports ist bewusst **nicht** Teil des Vergleichs — sonst
würde jedes erneute Hochladen ohne inhaltliche Änderung eine Nachricht auslösen.

## Unterrichtszeiten

Die Zeiten stehen im selben XML und kommen damit direkt von der Schule statt aus
einer abgetippten Tabelle:

| Quelle | Bedeutung |
|---|---|
| `<Std><Beg>` / `<End>` | Zeit der einzelnen Stunde, falls Indiware sie mitschickt |
| `<KlStunden><KlSt ZeitVon= ZeitBis=>` | Zeitraster der Klasse, als Rückfallebene je Stundennummer |

Jeder Tagesblock bekommt dadurch eine Kopfzeile „von wann bis wann habe ich
heute“, und jede Stundenzeile ihr eigenes Zeitfenster. Ausgefallene Stunden am
Rand zählen nicht mit — fällt die erste Stunde aus, verschiebt sich der Beginn
und die Meldung nennt den alten Wert in Klammern.

Liefert der Export ausnahmsweise gar keine Zeiten, bleiben die Meldungen wie
bisher ohne Uhrzeiten. Als Notnagel lässt sich das Raster dann über die Variable
`PLAN_ZEITEN` fest hinterlegen (siehe unten).

## Nachrichtenformat

```
Vertretungsplan FOS25W

📅 Freitag, 03. Juli 2026
🕐 09:25–13:50 (3.-8. Std)   (vorher 07:30–13:50)
🔴 2. Std (08:20–09:05): KU Herr Natusch fällt aus
🟡 4. Std (10:15–11:00): MA bei ENG in A163   (vorher: KU NAT A211)
✅ 6. Std (12:10–12:55): Änderung aufgehoben (VBR fällt aus)
ℹ️ neu: FOS25S: Zeugnisausgabe: 2.Stunde, CRS, A163
```

## Konfiguration

Unter *Settings → Secrets and variables → Actions*:

**Secrets**

| Name | Wert |
|---|---|
| `PLAN_USER` | Benutzername für `/vertret/` |
| `PLAN_PASS` | Passwort für `/vertret/` |
| `TG_TOKEN` | Bot-Token von @BotFather |
| `TG_CHAT` | eigene Telegram-Chat-ID |

**Variablen** (optional)

| Name | Wert |
|---|---|
| `PLAN_KLASSE` | Klassenkürzel, Standard `FOS25W` |
| `PLAN_ZEITEN` | festes Zeitraster als Rückfallebene, z. B. `1=07:30-08:15,2=08:20-09:05,…`. Leer lassen, solange die Zeiten aus dem XML kommen. |

## Lokal testen

```bash
export PLAN_USER=... PLAN_PASS=... PLAN_KLASSE=FOS25W
python3 watcher.py --dry-run          # zeigt nur an, was gesendet würde
python3 watcher.py --dry-run --force  # kompletter Plan statt nur Änderungen
python3 watcher.py --init             # Stand speichern, ohne zu melden
```

Nur Python-Standardbibliothek, keine Installation nötig.

## Wartung

- **Passwort geändert?** Secret `PLAN_PASS` neu setzen, sonst bricht der Lauf mit
  `401` ab und du bekommst eine Fehlermail von GitHub.
- **Andere Klasse?** Variable `PLAN_KLASSE` ändern.
- **Zu viele/zu wenige Prüfungen?** `cron` in `.github/workflows/watch.yml`
  anpassen — der Ausdruck ist in **UTC**, deutsche Zeit ist +2 h (Sommer) bzw. +1 h.
- **Verbrauch:** ca. 39 Läufe pro Schultag à 1 abgerechneter Minute ≈ 850 Minuten
  im Monat. Das Gratiskontingent für private Repos liegt bei 2000 Minuten.
- GitHub deaktiviert Cron-Workflows nach 60 Tagen ohne Repo-Aktivität. Die
  automatischen `state/`-Commits zählen als Aktivität, der Watcher hält sich also
  selbst am Leben — außer in langen Ferien ohne jede Planänderung. Nach den
  Sommerferien also einmal kurz prüfen, ob der Workflow noch aktiv ist.
