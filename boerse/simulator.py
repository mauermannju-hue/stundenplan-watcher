#!/usr/bin/env python3
"""Papierhandel im Livebetrieb: echte Kurse, fiktives Geld, echtes Protokoll.

Laeuft einmal je Handelstag nach Boersenschluss. Der Ablauf entspricht Zeile
fuer Zeile dem Backtest - nur dass die Zukunft hier tatsaechlich unbekannt ist.
Genau deshalb ist dieser Lauf aussagekraeftiger als jeder Backtest.

Der Zustand liegt in state/boerse.json und wird vom Workflow zurueck ins Repo
geschrieben. Faellt ein Lauf aus, holt der naechste die fehlenden Tage nach.

Aufruf:
    python3 -m boerse.simulator                 # Tageslauf
    python3 -m boerse.simulator --bericht       # nur Stand ausgeben
    python3 -m boerse.simulator --trockenlauf   # rechnen, nichts speichern
"""

import argparse
import html
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

from . import konfig, kurse, motor, portfolio, strategien
from .konfig import TIMEOUT, USER_AGENT
from .portfolio import Depot


def eur(betrag):
    """Deutsche Schreibweise: 1.234,56 EUR."""
    text = f"{betrag:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")
    return f"{text} EUR"


def prozent(anteil):
    return f"{anteil:+.1f} %".replace(".", ",")


def datum_de(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return iso or "-"


# --------------------------------------------------------------------------- Bericht

def bericht(depot, letzte_kurse, protokoll, beschriftung, hinweise=(), fuer_telegram=True):
    """Den Depotstand formulieren - als HTML fuer Telegram oder als Klartext.

    Beide Fassungen entstehen aus derselben Quelle. Wer den Text hinterher mit
    str.replace von Tags befreit, bekommt die HTML-Maskierung nicht mehr los und
    liest dann "-&gt;" statt "->".
    """
    schutz = html.escape if fuer_telegram else (lambda text: text)
    fett = (lambda text: f"<b>{text}</b>") if fuer_telegram else (lambda text: text)
    kursiv = (lambda text: f"<i>{text}</i>") if fuer_telegram else (lambda text: text)
    und = "&amp;" if fuer_telegram else "&"
    wert = depot.wert(letzte_kurse)
    rendite = (wert / depot.startkapital - 1) * 100 if depot.startkapital else 0.0
    zeilen = [
        fett(f"📊 Depot {datum_de(depot.letzter_handelstag)}"),
        kursiv(schutz(beschriftung)),
        "",
        f"Depotwert   {fett(eur(wert))}  ({prozent(rendite)})",
    ]

    vergleich = motor.vergleichswert(depot, letzte_kurse)
    if vergleich is not None:
        rendite_bh = (vergleich / depot.startkapital - 1) * 100
        differenz = wert - vergleich
        zeilen.append(f"Kaufen {und} Halten  {eur(vergleich)}  ({prozent(rendite_bh)})"
                      f"  →  {prozent(differenz / depot.startkapital * 100)}")
    zeilen.append(f"Bargeld  {eur(depot.bargeld)}   Gebühren bisher  {eur(depot.gebuehren_gesamt)}")
    tage = len(depot.verlauf)
    zeilen.append(f"Seit {datum_de(depot.start_datum)}, {tage} "
                  f"{'Handelstag' if tage == 1 else 'Handelstage'}")

    if depot.positionen:
        zeilen += ["", fett("Positionen")]
        for pos in sorted(depot.positionen.values(), key=lambda p: p.kuerzel):
            aktuell = letzte_kurse.get(pos.kuerzel, pos.einstand)
            gewinn = (aktuell - pos.einstand) * pos.stueck
            veraenderung = (aktuell / pos.einstand - 1) * 100 if pos.einstand else 0.0
            zeilen.append(
                f"  {pos.kuerzel}  {pos.stueck} × {pos.einstand:.2f} → {aktuell:.2f}   "
                f"{fett(f'{gewinn:+.2f} EUR')} ({prozent(veraenderung)})   Stop {pos.stop:.2f}")
    else:
        zeilen += ["", "Keine offenen Positionen — vollständig in Bargeld."]

    for tag, ereignisse in protokoll:
        zeilen += ["", fett(datum_de(tag))]
        zeilen += [f"  {schutz(e)}" for e in ereignisse]

    if depot.auftraege:
        zeilen += ["", fett("Zur nächsten Eröffnung geplant")]
        for auftrag in depot.auftraege:
            zeilen.append(f"  {auftrag.aktion.upper()} {auftrag.kuerzel} — "
                          f"{schutz(auftrag.grund)}")

    if hinweise:
        zeilen += [""] + [f"⚠ {schutz(h)}" for h in hinweise]

    zeilen += ["", kursiv("Fiktives Geld. Kein Anlageratschlag.")]
    return "\n".join(zeilen)


# --------------------------------------------------------------------------- Telegram

def teile_nachricht(text, limit=3900):
    """Telegram deckelt bei 4096 Zeichen - an Zeilengrenzen schneiden."""
    if len(text) <= limit:
        return [text]
    stuecke, aktuell = [], ""
    for zeile in text.split("\n"):
        if len(aktuell) + len(zeile) + 1 > limit:
            stuecke.append(aktuell.rstrip())
            aktuell = ""
        aktuell += zeile + "\n"
    if aktuell.strip():
        stuecke.append(aktuell.rstrip())
    return stuecke


def sende(token, chat_id, text):
    for stueck in teile_nachricht(text):
        payload = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": stueck,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Telegram lehnte ab ({exc.code}): {exc.read().decode()[:300]}")


# --------------------------------------------------------------------------- Lauf

def depot_anlegen(strategie_name):
    return Depot(startkapital=konfig.STARTKAPITAL, bargeld=konfig.STARTKAPITAL,
                 strategie=strategie_name)


def altersnotiz(daten):
    """Warnen, wenn die juengsten Kurse alt sind - sonst handelt man Gespenster."""
    if not daten:
        return None
    neuestes = max(kerzen[-1].datum for kerzen in daten.values())
    try:
        alter = (date.today() - datetime.strptime(neuestes, "%Y-%m-%d").date()).days
    except ValueError:
        return None
    if alter > 4:
        return (f"Neuester Kurs ist vom {datum_de(neuestes)} und damit {alter} Tage alt - "
                f"Kursquelle prüfen.")
    return None


def main():
    parser = argparse.ArgumentParser(description="Boersen-Simulator: ein Handelstag")
    parser.add_argument("--bericht", action="store_true", help="nur den Stand ausgeben")
    parser.add_argument("--trockenlauf", action="store_true", help="nichts speichern, nichts senden")
    parser.add_argument("--nur-cache", action="store_true", help="nicht ins Netz, nur state/kurse")
    parser.add_argument("--strategie", default=konfig.STRATEGIE)
    parser.add_argument("--ab", default="", metavar="JJJJ-MM-TT",
                        help="beim ersten Lauf ab diesem Tag nachbuchen statt erst heute")
    args = parser.parse_args()

    beschriftung, funktion = strategien.waehle(args.strategie)
    depot = portfolio.laden() or depot_anlegen(args.strategie)
    hinweise = []

    if depot.strategie and depot.strategie != args.strategie:
        hinweise.append(f"Strategie gewechselt: {depot.strategie} → {args.strategie}. "
                        f"Die bisherige Bilanz vermischt damit zwei Regelwerke.")
        depot.strategie = args.strategie

    print("Kurse holen ...")
    daten, ausgefallen = kurse.hole_alle(konfig.WATCHLIST, konfig.HISTORIE_TAGE,
                                         konfig.QUELLE, args.nur_cache, log=print)
    if not daten:
        raise SystemExit("Keine Kursdaten - Lauf abgebrochen, Zustand unveraendert.")
    if ausgefallen:
        hinweise.append(f"Ohne Kurse und heute nicht handelbar: {', '.join(ausgefallen)}")
    alt = altersnotiz(daten)
    if alt:
        hinweise.append(alt)

    letzte_kurse = {kuerzel: kerzen[-1].schluss for kuerzel, kerzen in daten.items()}

    if args.bericht:
        print(bericht(depot, letzte_kurse, [], beschriftung, hinweise, fuer_telegram=False))
        return

    ab = depot.letzter_handelstag
    if not ab:
        # Erster Lauf: heute anfangen, nicht die halbe Cache-Historie nachspielen.
        # Wer bewusst rueckwirkend starten will, sagt es mit --ab.
        tage = motor.zeitachse(daten)
        ab = args.ab or (tage[-2] if len(tage) > 1 else "")
        if args.ab:
            print(f"Erster Lauf: buche ab {datum_de(args.ab)} nach.")
    protokoll, _ = motor.spanne_verarbeiten(depot, daten, motor.Regeln(), funktion,
                                            ab=ab or None)
    if not protokoll and not depot.verlauf:
        print("Keine neuen Handelstage.")

    print()
    print(bericht(depot, letzte_kurse, protokoll, beschriftung, hinweise, fuer_telegram=False))
    text = bericht(depot, letzte_kurse, protokoll, beschriftung, hinweise)

    if args.trockenlauf:
        print("\nTrockenlauf: Zustand nicht gespeichert.")
        return

    portfolio.speichern(depot)
    token, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT")
    if token and chat:
        sende(token, chat, text)
        print("\nBericht an Telegram geschickt.")
    else:
        print("\nTG_TOKEN/TG_CHAT nicht gesetzt - kein Telegram-Versand.")


if __name__ == "__main__":
    main()
