#!/usr/bin/env python3
"""Strategien gegen die Vergangenheit laufen lassen.

Der Backtest benutzt exakt denselben Motor wie der Livebetrieb - gleiche
Gebuehren, gleiche Stops, gleiche Verzoegerung zwischen Signal und Ausfuehrung.
Was hier steht, ist deshalb kein Werbeprospekt, sondern die untere Grenze
dessen, was schiefgehen kann.

Wichtig bleibt trotzdem: ein guter Backtest beweist nichts. Er zeigt nur, dass
eine Regel in einer bestimmten Vergangenheit funktioniert haette. Je oefter man
Parameter dreht, bis das Ergebnis gefaellt, desto wertloser wird die Zahl.

Aufruf:
    python3 -m boerse.backtest                 # konfigurierte Strategie
    python3 -m boerse.backtest --alle          # alle drei im Vergleich
    python3 -m boerse.backtest --jahre 3
"""

import argparse

from . import konfig, kurse, motor, strategien
from .portfolio import Depot


def zeitachse(daten):
    """Weiterhin hier erreichbar, damit Aufrufer nicht in den Motor greifen muessen."""
    return motor.zeitachse(daten)


def laufen(daten, strategie, startkapital, regeln, strategie_name=""):
    """Einen kompletten Backtest rechnen und das fertige Depot zurueckgeben."""
    depot = Depot(startkapital=startkapital, bargeld=startkapital, strategie=strategie_name)
    _, tage_im_markt = motor.spanne_verarbeiten(depot, daten, regeln, strategie)
    return depot, tage_im_markt


def kennzahlen(depot, daten, tage_im_markt):
    """Aus dem Depot die Zahlen ziehen, auf die es ankommt."""
    letzte = {kuerzel: kerzen[-1].schluss for kuerzel, kerzen in daten.items()}
    endwert = depot.wert(letzte)
    verkaeufe = [b for b in depot.historie if b["aktion"] == "verkaufen"]
    gewinner = [b for b in verkaeufe if b["gewinn"] > 0]
    verlierer = [b for b in verkaeufe if b["gewinn"] <= 0]

    hoch, tiefster_einbruch = depot.startkapital, 0.0
    for _, wert in depot.verlauf:
        hoch = max(hoch, wert)
        if hoch > 0:
            tiefster_einbruch = max(tiefster_einbruch, (hoch - wert) / hoch)

    tage = len(depot.verlauf) or 1
    return {
        "endwert": endwert,
        "rendite": (endwert / depot.startkapital - 1) * 100,
        "vergleich": motor.vergleichswert(depot, letzte),
        "trades": len(verkaeufe),
        "trefferquote": (len(gewinner) / len(verkaeufe) * 100) if verkaeufe else 0.0,
        "schnitt_gewinn": (sum(b["gewinn"] for b in gewinner) / len(gewinner)) if gewinner else 0.0,
        "schnitt_verlust": (sum(b["gewinn"] for b in verlierer) / len(verlierer)) if verlierer else 0.0,
        "einbruch": tiefster_einbruch * 100,
        "gebuehren": depot.gebuehren_gesamt,
        "gebuehren_anteil": depot.gebuehren_gesamt / depot.startkapital * 100,
        "im_markt": tage_im_markt / tage * 100,
        "von": depot.verlauf[0][0] if depot.verlauf else "",
        "bis": depot.verlauf[-1][0] if depot.verlauf else "",
        "handelstage": tage,
    }


def bericht(name, beschriftung, zahlen, startkapital):
    zeilen = [
        f"=== {beschriftung} ({name}) ===",
        f"Zeitraum          {zahlen['von']} bis {zahlen['bis']}  ({zahlen['handelstage']} Handelstage)",
        f"Startkapital      {startkapital:>10.2f} EUR",
        f"Endkapital        {zahlen['endwert']:>10.2f} EUR   ({zahlen['rendite']:+.1f} %)",
    ]
    if zahlen["vergleich"] is not None:
        differenz = zahlen["endwert"] - zahlen["vergleich"]
        rendite_bh = (zahlen["vergleich"] / startkapital - 1) * 100
        zeilen.append(
            f"Kaufen & Halten   {zahlen['vergleich']:>10.2f} EUR   ({rendite_bh:+.1f} %)"
            f"   Differenz {differenz:+.2f} EUR")
    zeilen += [
        f"Trades            {zahlen['trades']:>10}      Trefferquote {zahlen['trefferquote']:.0f} %",
        f"Schnitt Gewinner  {zahlen['schnitt_gewinn']:>10.2f} EUR   "
        f"Schnitt Verlierer {zahlen['schnitt_verlust']:.2f} EUR",
        f"Groesster Einbruch{zahlen['einbruch']:>10.1f} %      im Markt an {zahlen['im_markt']:.0f} % der Tage",
        f"Gebuehren gesamt  {zahlen['gebuehren']:>10.2f} EUR   "
        f"({zahlen['gebuehren_anteil']:.1f} % des Startkapitals)",
    ]
    return "\n".join(zeilen)


def kuerzen(daten, jahre):
    """Historie auf die letzten n Jahre eindampfen (250 Handelstage je Jahr)."""
    if not jahre:
        return daten
    grenze = int(jahre * 250)
    return {k: kerzen[-grenze:] for k, kerzen in daten.items() if len(kerzen) > 60}


def main():
    parser = argparse.ArgumentParser(description="Handelsstrategien gegen echte Kurshistorie testen")
    parser.add_argument("--strategie", default=konfig.STRATEGIE, help="sma, rsi oder ausbruch")
    parser.add_argument("--alle", action="store_true", help="alle Strategien vergleichen")
    parser.add_argument("--jahre", type=float, default=0, help="nur die letzten n Jahre")
    parser.add_argument("--startkapital", type=float, default=konfig.STARTKAPITAL)
    parser.add_argument("--nur-cache", action="store_true", help="nicht ins Netz, nur state/kurse")
    args = parser.parse_args()

    print("Kurse holen ...")
    daten, ausgefallen = kurse.hole_alle(konfig.WATCHLIST, 10000, konfig.QUELLE,
                                         args.nur_cache, log=print)
    if not daten:
        raise SystemExit("Keine Kursdaten - Backtest nicht moeglich.")
    if ausgefallen:
        print(f"Ohne Daten und damit nicht im Test: {', '.join(ausgefallen)}")
    daten = kuerzen(daten, args.jahre)

    regeln = motor.Regeln()
    namen = sorted(strategien.KATALOG) if args.alle else [args.strategie]
    print(f"\nWatchlist: {', '.join(sorted(daten))}")
    print(f"Regeln: {regeln.gebuehr:.2f} EUR je Order, {regeln.slippage * 100:.2f} % Slippage, "
          f"Stop {regeln.stop_prozent * 100:.0f} %, max. {regeln.max_positionen} Positionen\n")

    for name in namen:
        beschriftung, funktion = strategien.waehle(name)
        depot, im_markt = laufen(daten, funktion, args.startkapital, regeln, name)
        print(bericht(name, beschriftung, kennzahlen(depot, daten, im_markt), args.startkapital))
        print()


if __name__ == "__main__":
    main()
