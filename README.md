# Stundenplan-Watcher — BSZ Görlitz → Telegram

Zwei Telegram-Benachrichtigungen, beide aus GitHub Actions und damit unabhängig
vom eigenen Rechner:

1. **Änderungsmeldung** — prüft den Plan der Klasse **FOS25W** alle 20 Minuten und
   meldet sich, sobald sich etwas ändert (`watch.yml`).
2. **Tagesplan** — schickt jeden Schultag gegen 06:12 Uhr den kompletten Plan der
   Klasse **SOZ25** samt Klingelplan (`daily.yml`).

## Wie es funktioniert

Der Plan ist ein **Indiware-mobil**-Export unter
`https://www.bszgoerlitz.de/vertret/mobil/` hinter HTTP-Basic-Auth. Dahinter
liegen saubere XML-Dateien — es muss also kein HTML gescrapt werden:

| Datei | Inhalt |
|---|---|
| `mobdaten/Klassen.xml` | der aktuellste Tagesplan + Liste aller schulfreien Tage |
| `mobdaten/PlanKl<JJJJMMTT>.xml` | Tagesplan für ein bestimmtes Datum |

`<Pl>` enthält den **kompletten Tagesplan**, nicht nur die Vertretungen. Was daran
tatsächlich geändert wurde, verraten die Attribute `FaAe` / `LeAe` / `RaAe` an den
Feldern — danach richtet sich das Symbol in der Nachricht.

`watcher.py` holt `Klassen.xml`, überspringt anhand der `FreieTage`-Liste alle
Ferien-, Feiertags- und Wochenendtermine und probiert die nächsten 14 Tage
durch. Aus jedem Plan werden der `<Kl>`-Block der eigenen Klasse und die
`<ZusatzInfo>`-Zeilen (schulweite Hinweise) gelesen, mit `state/last.json`
verglichen und nur echte Unterschiede gemeldet.

Der Zeitstempel des Exports ist bewusst **nicht** Teil des Vergleichs — sonst
würde jedes erneute Hochladen ohne inhaltliche Änderung eine Nachricht auslösen.

## Unterrichtszeiten

Die Zeiten stehen direkt an jeder Stunde, Quelle ist
[uzeiten.htm](https://bszgoerlitz.de/info/uzeiten.htm). Alt- und Neubau
unterscheiden sich **einzig in der 6. Stunde** (11:45–12:30 gegen 12:05–12:50).

Welches Gebäude gilt, steht nicht im Plan — es steckt im Raumnamen: `A…` ist Altbau,
`N…` Neubau (`AHL`, `AHR` zählen als Altbau). Das wird **je Stunde** ausgewertet, denn
eine Klasse wechselt im Tagesverlauf zwischen den Gebäuden. Ist der Raum leer — der
Normalfall bei Ausfall — entscheidet das mehrheitliche Gebäude des Tages; ist auch das
unklar, nennt die Nachricht beide Zeiten.

Der komplette Klingelplan hängt einmal täglich unter dem Tagesplan, nicht unter jeder
Änderungsmeldung.

## Nachrichtenformat

```
Vertretungsplan FOS25W

📅 Donnerstag, 20. August 2026
• 3. Std (08:50–09:35): BIO bei SDR in A141        regulär
🟡 4. Std (09:55–10:40): MA bei ENG in A163        geändert   (vorher: KU NAT A211)
🔴 6. Std (11:45–12:30): VBR Frau Pfeiffer-Krause fällt aus
✅ 7. Std: Änderung aufgehoben (VBR fällt aus)
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

**Variable** (optional)

| Name | Wert |
|---|---|
| `PLAN_KLASSE` | Klasse der Änderungsmeldung, Standard `FOS25W` |
| `PLAN_KLASSE2` | Klasse des Tagesplans, Standard `SOZ25` |
| `DAILY_HOUR` | Stunde des Tagesplans in Berliner Zeit, Standard `6` |

## Lokal testen

```bash
export PLAN_USER=... PLAN_PASS=... PLAN_KLASSE=FOS25W
python3 watcher.py --dry-run          # zeigt nur an, was gesendet würde
python3 watcher.py --dry-run --force  # kompletter Plan statt nur Änderungen
python3 watcher.py --init             # Stand speichern, ohne zu melden
python3 watcher.py --dry-run --daily  # Tagesplan der zweiten Klasse ansehen
```

Nur Python-Standardbibliothek, keine Installation nötig.

## Wartung

- **Passwort geändert?** Secret `PLAN_PASS` neu setzen, sonst bricht der Lauf mit
  `401` ab und du bekommst eine Fehlermail von GitHub.
- **Andere Klasse?** Variable `PLAN_KLASSE` bzw. `PLAN_KLASSE2` ändern.
- **Tagesplan zu früh/spät?** `DAILY_HOUR` setzen. Der Cron in `daily.yml` feuert
  bewusst zweimal (04:12 und 05:12 UTC); das Skript prüft die Berliner Stunde und
  bricht beim falschen Lauf ab — so stimmt die Uhrzeit ganzjährig trotz Sommerzeit.
- **Klasse steht nicht im Plan?** Dann meldet der Tagesplan das ausdrücklich. Nicht
  jede Klasse hat jeden Tag Unterricht (Blockunterricht, Praxisphasen).
- **Zu viele/zu wenige Prüfungen?** `cron` in `.github/workflows/watch.yml`
  anpassen — der Ausdruck ist in **UTC**, deutsche Zeit ist +2 h (Sommer) bzw. +1 h.
- **Verbrauch:** ca. 39 Läufe pro Schultag à 1 abgerechneter Minute ≈ 850 Minuten
  im Monat. Das Gratiskontingent für private Repos liegt bei 2000 Minuten.
- GitHub deaktiviert Cron-Workflows nach 60 Tagen ohne Repo-Aktivität. Die
  automatischen `state/`-Commits zählen als Aktivität, der Watcher hält sich also
  selbst am Leben — außer in langen Ferien ohne jede Planänderung. Nach den
  Sommerferien also einmal kurz prüfen, ob der Workflow noch aktiv ist.
