# Inspo-Inbox – Inspiration rein, Stil-Bibliothek raus

Erste Version des „kreativen Gedächtnisses“ für den KI-Mitarbeiter. Der Bot sitzt als
Mitglied in der Inspo-Gruppe und nimmt auch Privatnachrichten an. Er schaut sich alles an, was das Team
beim Scrollen findet, sortiert Irrelevantes aus und zerlegt die guten Sachen: Hook, Schnitt, Captions und Schrift,
Look und Sound. Bei Websites sind es Layout, Typografie, Farben und Conversion. Jede übertragbare Technik landet
in einer **Stil-Bibliothek**, die als Claude-Skill auf neue Projekte angewendet werden kann.

## Ablauf

```
Telegram (Inspo-Gruppe / Privatchat)        WhatsApp-/Telegram-Export (Altbestand)
          │  Link · Video-Datei · Idee                   │  python -m inspo import
          ▼                                              ▼
   ┌────────────────────── Warteschlange (SQLite) ───────────────────────┐
   │ Duplikate werden erkannt (gleicher Reel 3x geteilt = starkes Signal)│
   └──────────────────────────────────┬──────────────────────────────────┘
          ┌───────────────────────────┼─────────────────────────┐
        Video                      Website                     Idee
   yt-dlp → ffmpeg:            Playwright:                 Claude:
   Dauer, Format, Schnitte,    Screenshots Desktop+Handy,  Einschätzung,
   Schlüsselbilder,            gemessene Schriften,        Möglichkeiten,
   (Whisper-Transkript)        Farben, Überschriften       nächste Schritte
          │                           │
   ① Vorfilter: 4 Bilder,             │
     Relevanz 0–10                    │
     < Schwelle → 🥱 verworfen        │
          │                           │
   ② Breakdown: 16 Bilder +    Breakdown
     Messwerte + Transkript           │
          └─────────────┬─────────────┘
                        ▼
      Techniken (gut ✅ / Anti-Muster ⛔) + Caption-Presets
      → techniken-Tabelle (gleiche Technik = Zähler +1)
      → bibliothek/eintraege/<nr>-<titel>/README.md (Breakdown + Bilder)
      → bibliothek/STIL-BIBLIOTHEK.md
      → Claude-Skill „stil-bibliothek“ (python -m inspo skill …)
                        ▼
      /vorschlag <Briefing>: Bibliothek → 2–3 Stil-Varianten für ein neues Kundenprojekt
```

**Warum zweistufig?** In der Gruppe landet viel. Der Vorfilter sieht nur 4 Bilder, ist entsprechend billig und
schnell und lässt nur Relevantes in den teuren Breakdown mit 16 Bildern. Was er falsch aussortiert,
holt `/analyse <Nr>` zurück.

**Woher kennt Claude „unseren“ Maßstab?** Aus `profil.md`. Dort steht, wer wir sind, für wen wir
produzieren und wie unser aktueller Stil aussieht. **Diese Datei bitte einmal anpassen.** Sie ist der
wichtigste Hebel für gute Bewertungen.

**Wie bleibt die Bibliothek sauber?** Bei jeder Analyse bekommt Claude die Liste der bereits bekannten
Techniken mit und benennt Wiederholungen gleich. Gleiche Namen werden zusammengeführt, die Zahl in
Klammern zeigt dann, wie oft das Team eine Technik gut fand.

## Einrichtung auf dem Tower (ca. 20 Minuten)

```bash
# 1) Systempakete
sudo apt install ffmpeg python3-venv
# 2) Code + Abhängigkeiten
git clone <dieses repo> /opt/stundenplan-watcher && cd /opt/stundenplan-watcher
python3 -m venv .venv && .venv/bin/pip install -r inspo/requirements.txt
.venv/bin/python -m playwright install --with-deps chromium
# optional Transkripte (lokal, kostenlos):  .venv/bin/pip install faster-whisper
# 3) Konfiguration
cp inspo/.env.beispiel inspo/.env   # ausfüllen (siehe unten)
```

**Telegram-Bot anlegen:**
1. In Telegram **@BotFather** → `/newbot` → Name z.B. „Inspo“ → Token in `INSPO_TG_TOKEN`.
2. BotFather → `/setprivacy` → Bot wählen → **Disable**. Sonst sieht der Bot in Gruppen nur Befehle,
   keine normalen Nachrichten mit Links.
3. Bot in die Inspo-Gruppe einladen und dort `/id` schreiben. Die Chat-ID (beginnt mit `-100…`) plus die
   eigenen Privatchat-IDs kommen in `INSPO_TG_CHATS`. Ohne diese Liste reagiert der Bot auf jeden Chat.
4. Starten: `.venv/bin/python -m inspo bot`. Für Dauerbetrieb `inspo/inspo.service` anpassen und als
   systemd-Dienst aktivieren.

**Instagram:** Instagram lässt anonyme Downloads oft nicht zu. Dann im Browser bei Instagram einloggen
(am besten mit einem Zweit-Account), die Cookies als `cookies.txt` exportieren (Erweiterung „Get
cookies.txt LOCALLY“) und den Pfad in `INSPO_COOKIES` eintragen. TikTok und YouTube Shorts klappen meist
ohne. `yt-dlp` regelmäßig aktualisieren (`pip install -U yt-dlp`), die Plattformen ändern sich ständig.

### Falls die Inspo-Gruppe bei WhatsApp ist

WhatsApp erlaubt keine Bots in normalen Gruppen. Es gibt drei Wege:
- **Empfohlen:** Die Inspo-Gruppe zu Telegram umziehen. Das Teilen aus Instagram oder TikTok
  funktioniert dort genauso („Teilen → Telegram → Inspo“).
- Die WhatsApp-Gruppe behalten und ab und zu exportieren (Gruppe → Chat exportieren → ohne Medien),
  dann: `python -m inspo import chat.txt --sofort`.
- Einzelne Sachen direkt an den Bot im Telegram-Privatchat schicken.

Der **Altbestand** lässt sich einmalig genauso einlesen, egal ob WhatsApp (`.txt`) oder Telegram Desktop
(„Chatverlauf exportieren“ → JSON → `result.json`). Mit `--seit 2026-01-01` nur einen Teil nehmen.
Doppelte Links werden automatisch erkannt.

## Bedienung

| Was | Wie |
|---|---|
| Reel/TikTok/Short merken | Link in die Gruppe oder an den Bot, Text dazu wird Kommentar („für Kunde X“) |
| Website merken | Link schicken (Nicht-Social-Links gelten automatisch als Website) |
| Idee loswerden | Privatchat: einfach schreiben · Gruppe: `/idee …` oder Bot erwähnen |
| Nur notieren | `/notiz …` |
| Bibliothek anwenden | `/vorschlag Imagefilm für Autohaus, 30 s Reel, junge Zielgruppe` |
| Nachschlagen | `/suche captions`, `/top`, `/details 12`, `/bibliothek`, `/status` |
| Aussortiertes doch analysieren | `/analyse 12` |

In der Gruppe antwortet der Bot standardmäßig **nur mit Reaktionen**, damit es dort ruhig bleibt:
👀 angekommen · 🔥 als Vorlage gemerkt · 👌 einzelne Techniken gemerkt · 🥱 nicht relevant · 🤷 Fehler.
Im Privatchat kommt die Zusammenfassung als Text. Mit `INSPO_GRUPPE_LEISE=0` schreibt er auch in der
Gruppe.

Ohne Telegram geht alles auch per Kommandozeile: `python -m inspo add <url>`, `idee`, `import`,
`verarbeite`, `vorschlag`, `suche`, `liste`, `bibliothek`, `skill <ordner>`.

## Anbindung an KI-Mitarbeiter und CRM

- **Als Skill:** `python -m inspo skill ~/.claude/skills/stil-bibliothek` schreibt `SKILL.md` und
  `references/` (Bibliothek und alle Breakdowns). Damit kennt der KI-Mitarbeiter unseren Stil, sobald es
  um Videos, Captions, Schnitt oder Websites geht. Am einfachsten per Cron nach jedem neuen Eintrag oder
  nächtlich neu exportieren.
- **Als Daten:** Alles liegt in `daten/inspo.db` (SQLite). Die Tabelle `eintraege` enthält Link, Absender,
  Kommentar, Score, Urteil, Tags und die vollständige Analyse als JSON in `analyse_json`. Die Tabelle
  `techniken` enthält die Bibliothek mit Kategorie, Bewertung, Anzahl, Beispielen und den
  Caption-Specs in `details_json`. Das CRM kann beides direkt lesen.
- **Als Funktion:** `from inspo.db import DB; from inspo.verarbeitung import verarbeite`. Der
  KI-Mitarbeiter kann so selbst Einträge anlegen und verarbeiten, wenn der Telegram-Kanal später in
  seinen eigenen Kanal übergeht. Ideen (`art='idee'`) eignen sich als Aufgaben oder Notizen im CRM.

## Kosten (grob geschätzt)

Standardmäßig läuft alles mit Claude Opus 5.5 ($4 / $20 je Mio. Tokens):
- Vorfilter: etwa 5k Eingabe-Tokens, also ungefähr **2–3 Cent pro Link**.
- Voller Video-Breakdown mit 16 Bildern: etwa 15–20k Eingabe-Tokens plus Antwort, also ungefähr
  **10–20 Cent pro relevantem Video**.

Wer den Vorfilter billiger will, setzt `INSPO_MODELL_TRIAGE=claude-haiku-5-5`. Bei großen Importen
vorher mit `verarbeite --limit 20` testen.

## Tests

```bash
python -m unittest discover -s inspo/tests -t . -v
```

Die Tests laufen offline gegen ein synthetisches Video, eine lokale Testseite, einen Fake-Telegram und
einen Fake-Claude-Server. Sie prüfen auch, dass die API-Anfrage richtig aufgebaut ist: strukturierte
Ausgabe, Effort, Fallback, Caching und Bilder.

## Grenzen der ersten Version / nächste Schritte

- Claude „sieht“ Videos als Einzelbilder und Messwerte. Bewegungen wie Speed Ramps oder Animationen
  erschließt es aus der Bildfolge. Die Schnittzeitpunkte werden exakt gemessen.
- Musik oder Sounds werden nur per Transkript erfasst, eine Musikerkennung fehlt noch.
  Möglicher Ausbau: Audio-Fingerprint, Lautstärkekurve, Beat-Erkennung für Schnitt-auf-Beat.
- Karussell-Posts, also Bild-Slides bei Instagram, werden noch nicht gesondert behandelt.
- Wer was geschickt hat, wird gespeichert, aber noch nicht ausgewertet. Eine Idee dafür ist ein
  Wochen-Digest „Das hat das Team diese Woche gesammelt“.
- Die Bibliothek führt Techniken über den exakten Namen zusammen. Bei sehr großen Mengen lohnt sich
  ein gelegentlicher Aufräumlauf, der Synonyme zusammenlegt.
