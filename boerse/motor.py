#!/usr/bin/env python3
"""Der Ablauf eines Handelstages - identisch im Backtest und im Livebetrieb.

Genau eine Stelle, an der gehandelt wird. Sonst wuerde der Backtest andere
Regeln testen als der Simulator spaeter anwendet, und das Ergebnis waere
wertlos.

Reihenfolge innerhalb eines Tages, und zwar chronologisch ehrlich:

  1. Eroeffnung  Auftraege von gestern ausfuehren
  2. Tagsueber   Stop-Loss pruefen (am Tagestief)
  3. Schluss     Strategie befragen -> Auftraege fuer morgen

Der springende Punkt ist Schritt 3: entschieden wird auf Basis des
Schlusskurses, gehandelt erst am naechsten Morgen. Wer stattdessen zum
Schlusskurs desselben Tages kauft, handelt mit Wissen, das er zum
Handelszeitpunkt noch nicht hatte. Solche Backtests sehen glaenzend aus und
halten im Echtbetrieb nichts.
"""

from dataclasses import dataclass

from . import konfig, strategien
from .portfolio import Auftrag


@dataclass
class Regeln:
    """Risiko- und Kostenrahmen. Vorbelegt aus konfig.py, im Test frei setzbar."""
    gebuehr: float = konfig.GEBUEHR_JE_ORDER
    slippage: float = konfig.SLIPPAGE
    max_positionen: int = konfig.MAX_POSITIONEN
    max_anteil: float = konfig.MAX_ANTEIL_JE_POSITION
    min_order: float = konfig.MIN_ORDERVOLUMEN
    stop_prozent: float = konfig.STOP_PROZENT


def schlusskurse(tagesdaten):
    return {kuerzel: kerzen[-1].schluss for kuerzel, kerzen in tagesdaten.items()}


def _kaufkurs(kerze, regeln):
    """Gekauft wird etwas ueber, verkauft etwas unter dem Eroeffnungskurs.

    Der Aufschlag steht fuer Spread und Slippage - beides existiert und beides
    zahlt man. Ein Test ohne diesen Aufschlag verspricht Renditen, die es an
    der Boerse nicht zu kaufen gibt.
    """
    return kerze.auf * (1 + regeln.slippage)


def _verkaufskurs(kerze, regeln):
    return kerze.auf * (1 - regeln.slippage)


def _auftraege_ausfuehren(depot, tagesdaten, datum, regeln):
    ereignisse = []
    # Verkaeufe zuerst: sie machen Bargeld frei, das derselbe Tag noch braucht.
    offen = sorted(depot.auftraege, key=lambda a: 0 if a.aktion == "verkaufen" else 1)
    depot.auftraege = []

    for auftrag in offen:
        kerzen = tagesdaten.get(auftrag.kuerzel)
        if not kerzen:
            # Kein Kurs heute (Feiertag am Handelsplatz, Datenluecke):
            # Auftrag verfaellt, statt ihn blind zu einem alten Kurs zu buchen.
            ereignisse.append(f"{auftrag.kuerzel}: Auftrag verfallen, kein Kurs am {datum}")
            continue
        heute = kerzen[-1]

        if auftrag.aktion == "verkaufen":
            buchung = depot.verkaufen(auftrag.kuerzel, _verkaufskurs(heute, regeln), datum,
                                      regeln.gebuehr, auftrag.grund)
            if buchung:
                ereignisse.append(
                    f"VERKAUF {auftrag.kuerzel}: {buchung['stueck']} zu "
                    f"{buchung['kurs']:.2f} -> {buchung['gewinn']:+.2f} EUR ({auftrag.grund})")
            continue

        if auftrag.kuerzel in depot.positionen:
            continue
        if not depot.frei_fuer_neue_position(regeln.max_positionen):
            ereignisse.append(f"{auftrag.kuerzel}: Kauf verworfen, "
                              f"{regeln.max_positionen} Positionen sind das Limit")
            continue

        kurs = _kaufkurs(heute, regeln)
        # Bewertung zur Eroeffnung, nicht zum Schluss: die Positionsgroesse darf
        # nur von Kursen abhaengen, die zum Zeitpunkt der Order schon feststehen.
        eroeffnungskurse = {k: kerzen_k[-1].auf for k, kerzen_k in tagesdaten.items()}
        depotwert = depot.wert(eroeffnungskurse)
        einsatz = min(depotwert * regeln.max_anteil, depot.bargeld - regeln.gebuehr)
        stueck = int(einsatz // kurs) if kurs > 0 else 0
        volumen = stueck * kurs
        if stueck <= 0 or volumen < regeln.min_order:
            ereignisse.append(
                f"{auftrag.kuerzel}: Kauf verworfen, Ordervolumen {volumen:.2f} EUR unter "
                f"Mindestgroesse {regeln.min_order:.0f} EUR - die Gebuehr waere zu teuer")
            continue

        buchung = depot.kaufen(auftrag.kuerzel, kurs, stueck, datum, regeln.gebuehr,
                               auftrag.grund, kurs * (1 - regeln.stop_prozent),
                               depot.strategie)
        if buchung:
            ereignisse.append(
                f"KAUF {auftrag.kuerzel}: {stueck} zu {kurs:.2f} = {volumen:.2f} EUR "
                f"({auftrag.grund})")
    return ereignisse


def _stops_pruefen(depot, tagesdaten, datum, regeln):
    """Reissleine. Laeuft vor der Strategie - Risiko schlaegt Meinung."""
    ereignisse = []
    for kuerzel in list(depot.positionen):
        kerzen = tagesdaten.get(kuerzel)
        if not kerzen:
            continue
        heute = kerzen[-1]
        pos = depot.positionen[kuerzel]
        if heute.tief > pos.stop:
            continue
        # Eroeffnet der Kurs bereits unter dem Stop, gibt es den Stop-Preis
        # nicht - dann wird zur Eroeffnung ausgefuehrt. Die Luecke nach unten
        # ist der Grund, warum ein Stop kein Sicherheitsversprechen ist.
        kurs = min(pos.stop, heute.auf)
        buchung = depot.verkaufen(kuerzel, kurs, datum, regeln.gebuehr,
                                  f"Stop-Loss bei {pos.stop:.2f}")
        ereignisse.append(
            f"STOP {kuerzel}: {buchung['stueck']} zu {buchung['kurs']:.2f} -> "
            f"{buchung['gewinn']:+.2f} EUR")
    return ereignisse


def _signale_sammeln(depot, tagesdaten, datum, regeln, strategie):
    """Strategie zum Schlusskurs befragen und Auftraege fuer morgen anlegen."""
    signale = []
    for kuerzel, kerzen in sorted(tagesdaten.items()):
        signal = strategie(kerzen, kuerzel in depot.positionen)
        if signal.richtung != "halten":
            signale.append((kuerzel, signal))

    # Erst alle Signale einsammeln, dann verteilen: ein Verkauf gibt morgen
    # einen Platz frei, egal ob das Papier alphabetisch vor oder hinter dem
    # Kaufkandidaten steht.
    verkaeufe = [(k, s) for k, s in signale if s.richtung == "verkaufen"]
    kaeufe = [(k, s) for k, s in signale if s.richtung == "kaufen"]
    frei = regeln.max_positionen - len(depot.positionen) + len(verkaeufe)

    ereignisse = []
    for kuerzel, signal in verkaeufe + kaeufe:
        if signal.richtung == "kaufen":
            if frei <= 0:
                ereignisse.append(f"Signal KAUFEN {kuerzel} verworfen: kein Platz "
                                  f"({regeln.max_positionen} Positionen sind das Limit)")
                continue
            frei -= 1
        depot.auftraege.append(Auftrag(kuerzel, signal.richtung, signal.grund, datum))
        zahlen = "  ".join(f"{name} {wert}" for name, wert in signal.kennzahlen.items())
        ereignisse.append(f"Signal {signal.richtung.upper()} {kuerzel}: {signal.grund}"
                          + (f" [{zahlen}]" if zahlen else ""))
    return ereignisse


def handelstag(depot, tagesdaten, datum, regeln, strategie):
    """Einen kompletten Handelstag buchen. Liefert die Ereignisse als Text."""
    if not tagesdaten:
        return []

    if not depot.start_datum:
        depot.start_datum = datum
        # Vergleichsmassstab: was haette gleichmaessig verteiltes Kaufen und
        # Liegenlassen ergeben? Ohne diesen Anker ist jede Rendite bedeutungslos.
        depot.referenz = {k: kerzen[-1].auf for k, kerzen in tagesdaten.items()}

    ereignisse = []
    ereignisse += _auftraege_ausfuehren(depot, tagesdaten, datum, regeln)
    ereignisse += _stops_pruefen(depot, tagesdaten, datum, regeln)
    ereignisse += _signale_sammeln(depot, tagesdaten, datum, regeln, strategie)

    depot.letzter_handelstag = datum
    depot.verlauf.append([datum, depot.wert(schlusskurse(tagesdaten))])
    return ereignisse


def vergleichswert(depot, kurse):
    """Depotwert, wenn man am Starttag gleichmaessig gekauft und gehalten haette."""
    if not depot.referenz:
        return None
    je_wert = depot.startkapital / len(depot.referenz)
    summe = 0.0
    for kuerzel, startkurs in depot.referenz.items():
        aktuell = kurse.get(kuerzel)
        if aktuell is None or startkurs <= 0:
            summe += je_wert
            continue
        stueck = int(je_wert // (startkurs * (1 + konfig.SLIPPAGE)))
        rest = je_wert - stueck * startkurs * (1 + konfig.SLIPPAGE) - konfig.GEBUEHR_JE_ORDER
        summe += stueck * aktuell + max(rest, 0.0)
    return round(summe, 2)


def hole_strategie(name):
    """Name aus der Konfiguration in (Beschriftung, Funktion) aufloesen."""
    return strategien.waehle(name)


# --------------------------------------------------------------------------- Tagesschleife

def zeitachse(daten):
    """Alle Handelstage aller Papiere, aufsteigend und ohne Dubletten."""
    tage = set()
    for kerzen in daten.values():
        tage.update(k.datum for k in kerzen)
    return sorted(tage)


def datums_index(daten):
    """Je Papier: Datum -> Position in der Liste. Spart das Suchen im Verlauf."""
    return {kuerzel: {k.datum: i for i, k in enumerate(kerzen)}
            for kuerzel, kerzen in daten.items()}


def spanne_verarbeiten(depot, daten, regeln, strategie, ab=None, fenster=None):
    """Alle Handelstage nach `ab` buchen. Gemeinsamer Kern von Backtest und Livelauf.

    Auch der Livebetrieb laeuft hierdurch: faellt der Workflow drei Tage aus,
    holt der naechste Lauf die fehlenden Tage der Reihe nach nach, statt sie
    stillschweigend zu verschlucken.
    """
    fenster = konfig.INDIKATOR_FENSTER if fenster is None else fenster
    index = datums_index(daten)
    protokoll, tage_im_markt = [], 0

    for datum in zeitachse(daten):
        if ab and datum <= ab:
            continue
        tagesdaten = {}
        for kuerzel, kerzen in daten.items():
            i = index[kuerzel].get(datum)
            if i is not None:
                tagesdaten[kuerzel] = kerzen[max(0, i - fenster):i + 1]
        ereignisse = handelstag(depot, tagesdaten, datum, regeln, strategie)
        if ereignisse:
            protokoll.append((datum, ereignisse))
        if depot.positionen:
            tage_im_markt += 1
    return protokoll, tage_im_markt
