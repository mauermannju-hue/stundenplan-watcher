# Börsen-Simulator — Papierhandel auf Einzelaktien

Ein Lernprojekt im selben Repo, weil es dieselbe Mechanik nutzt wie der
Stundenplan-Watcher: GitHub Actions als Uhr, Python ohne Abhängigkeiten,
Zustand als JSON unter `state/`, Bericht per Telegram.

**Was das ist:** ein Depot aus fiktivem Geld, das mit echten Kursen und echten
Kosten nach festen Regeln handelt und jeden Handelstag Rechenschaft ablegt.

**Was das nicht ist:** eine Anlageempfehlung, eine Prognose oder ein Weg,
Geld zu verdienen. Es gibt hier keine Meinung zu Aktien — nur Regeln, die
stur ausgeführt und ehrlich abgerechnet werden.

## Der Tagesablauf

Einmal je Handelstag nach Börsenschluss:

| Zeitpunkt | Was passiert |
|---|---|
| Eröffnung | Aufträge von gestern werden ausgeführt |
| Tagsüber | Stop-Loss wird gegen das Tagestief geprüft |
| Schluss | Die Strategie sieht den Schlusskurs und legt Aufträge für **morgen** an |

Der dritte Punkt ist der wichtigste am ganzen Projekt. Entschieden wird auf
Basis des Schlusskurses, gekauft wird erst am nächsten Morgen zur Eröffnung.
Wer stattdessen zum Schlusskurs desselben Tages kauft, handelt mit Wissen, das
er zum Handelszeitpunkt nicht hatte. Solche Backtests sehen glänzend aus und
halten im Echtbetrieb nichts. `test_kein_blick_in_die_zukunft` in
`boerse/test_boerse.py` bewacht genau das — fällt dieser Test, sind alle
Renditezahlen des Projekts wertlos.

Backtest und Livebetrieb laufen durch **denselben** Motor (`boerse/motor.py`).
Sonst würde der Test andere Regeln prüfen als der Simulator später anwendet.

## Die drei Strategien

Sie widersprechen einander fundamental — das ist Absicht. Welche taugt,
entscheidet nicht die Überzeugung, sondern die Auswertung.

| Name | Idee | Kauft | Verkauft |
|---|---|---|---|
| `sma` | Trendfolge | SMA 20 kreuzt SMA 50 von unten | SMA 20 fällt unter SMA 50 |
| `rsi` | Rückkehr zum Mittelwert | RSI 14 dreht aus dem Bereich unter 30 | RSI 14 wieder über 55 |
| `ausbruch` | Momentum (Donchian) | Schluss über dem 20-Tage-Hoch | Schluss unter dem 10-Tage-Tief |

Dazu kommen Regeln, die über allem stehen — Risiko schlägt Meinung:

- höchstens **3** Positionen gleichzeitig, je höchstens **33 %** des Depots
- **Stop-Loss 6 %** unter dem Einstand, geprüft vor jedem Strategiesignal
- Mindestordervolumen **150 €**, sonst frisst die Gebühr den Trade
- **1 € je Order** plus **0,1 % Spread** je Seite — beides existiert, beides zahlt man

Alles einstellbar in `boerse/konfig.py` oder per Umgebungsvariable
(`BOERSE_STRATEGIE`, `BOERSE_STARTKAPITAL`, `BOERSE_GEBUEHR`, …).

## Einrichtung

Der Simulator braucht keine Secrets außer denen, die für Telegram ohnehin
schon im Repo liegen (`TG_TOKEN`, `TG_CHAT`). Kursdaten kommen ohne
Schlüssel von stooq.com, ersatzweise von Yahoo.

1. Actions-Tab → **Börsen-Simulator** → *Run workflow*. Strategie wählen,
   `trockenlauf` ankreuzen. Der Lauf holt Kurse und zeigt den Bericht, ohne
   etwas zu speichern.
2. Sieht das plausibel aus, denselben Lauf ohne `trockenlauf` starten. Damit
   entsteht `state/boerse.json` — ab hier läuft die Simulation.
3. Danach läuft sie werktags um 19:00 UTC von selbst.

Lokal geht dasselbe:

```bash
python3 -m boerse.simulator --trockenlauf     # rechnen, nichts anfassen
python3 -m boerse.simulator --bericht         # nur den Stand zeigen
python3 -m boerse.backtest --alle --jahre 3   # Strategien vergleichen
python3 -m unittest boerse.test_boerse -v     # 24 Tests
```

Geholte Kurse liegen als CSV unter `state/kurse/` (nicht im Git). Damit
laufen Backtests auch offline: `--nur-cache`.

## Wie lange laufen lassen?

**Mindestens einen Monat, eher zwei.** Der Grund steht in den Zahlen: über
knapp drei Jahre kommt jede der drei Strategien auf 27 bis 46 abgeschlossene
Trades — das sind **rund ein bis zwei Trades pro Monat**. Eine Woche liefert
mit hoher Wahrscheinlichkeit **null** Trades und damit exakt null Erkenntnis.

Auch ein Monat ist statistisch nichts. Zwei Trades sagen über die Güte einer
Strategie so viel aus wie zwei Münzwürfe über die Fairness der Münze. Was ein
Monat dagegen sehr wohl zeigt, und das ist den Aufwand wert:

- ob die Mechanik im Alltag trägt (Feiertage, Datenlücken, ausgefallene Läufe)
- wie sich Gebühren zum eingesetzten Kapital verhalten
- wie es sich anfühlt, eine Regel auch dann zu befolgen, wenn sie gerade falsch liegt

Für die Frage *welche Strategie ist besser* ist der Backtest über Jahre das
Werkzeug, nicht der Monat live.

## Wie man die Zahlen liest

**Der Vergleichsmaßstab ist Kaufen & Halten.** Jeder Bericht stellt daneben,
was gleichmäßig verteiltes Kaufen und Liegenlassen ergeben hätte. Eine
Strategie, die 4 % macht, während Kaufen & Halten 7 % macht, hat 3 % gekostet
— und dafür Arbeit, Gebühren und Nerven verbraucht.

**Gebühren sind kein Rundungsfehler.** Im Vergleichslauf kostete die
handelsaktivste Strategie über knapp drei Jahre 9,4 % des Startkapitals allein
an Ordergebühren. Bei kleinem Kapital entscheidet das über alles andere.

**Vorsicht mit dem Backtest.** Er beweist nichts. Er zeigt, dass eine Regel in
einer bestimmten Vergangenheit funktioniert hätte. Wer Parameter dreht, bis
das Ergebnis gefällt, hat die Vergangenheit auswendig gelernt und nichts über
die Zukunft erfahren. Faustregel: eine Regel, die nur bei SMA 20/50 funktioniert
und bei 19/48 zusammenbricht, ist Zufall.

## Bekannte Vereinfachungen

Ehrlichkeit über die Grenzen gehört zum Aufbau dazu:

- **Dividenden und Splits.** Eine Dividendenzahlung sieht in den Rohkursen aus
  wie ein Kursrutsch und kann einen Stop auslösen, obwohl niemand Geld verloren
  hat. Welche Bereinigung stooq und Yahoo genau liefern, ist nicht geprüft.
- **Eine Währung.** Die Standard-Watchlist ist bewusst rein deutsch und in Euro.
  Wer US-Werte einträgt, bekommt Dollar und Euro in einen Topf geworfen — die
  Rendite ist dann teilweise Wechselkurs.
- **Ausführung.** Es wird immer der volle Auftrag zum Eröffnungskurs plus
  Spread ausgeführt. In Wirklichkeit gibt es Teilausführungen, dünne Bücher und
  Eröffnungsauktionen.
- **Keine Steuern.** Abgeltungsteuer, Sparerpauschbetrag und die Regel, dass
  Aktienverluste nur mit Aktiengewinnen verrechenbar sind, bleiben außen vor.
  Bei echtem Geld verschiebt das die Rechnung spürbar.
- **Tagesdaten.** Innerhalb eines Tages weiß der Simulator nur Eröffnung, Hoch,
  Tief, Schluss. Ob das Tief vor oder nach dem Hoch kam, ist ihm unbekannt.

## Der Schritt zu echtem Geld

Der Code führt keine echten Orders aus und hat keinen Zugang zu einem Depot.
Das ist Absicht und ändert sich nicht nebenbei. Wer nach der Simulation
weitergehen will, braucht einen Broker mit API (in Deutschland realistisch:
Interactive Brokers, das auch ein kostenloses Paper-Trading-Konto anbietet) —
und sollte jede Order weiterhin selbst bestätigen.

Vorher lohnt der Blick auf die Studienlage zum kurzfristigen Handel von
Privatanlegern: die große Mehrheit schneidet schlechter ab als mit
Kaufen und Liegenlassen. Wer trotzdem einsteigt, setzt nur Geld ein, dessen
Totalverlust nicht wehtut, und behandelt das Ergebnis als Lehrgeld, nicht als
Anlage.
