#!/usr/bin/env python3
"""Das Depot: Bargeld, Positionen, offene Auftraege, Trade-Historie.

Rein mechanisch - hier wird nichts entschieden, nur gebucht. Wer kauft und
warum, steht in strategien.py und motor.py. Der Zustand liegt als JSON in
state/boerse.json und ist bewusst von Hand lesbar.
"""

import json
import os
from dataclasses import asdict, dataclass, field

ZUSTAND_PFAD = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "boerse.json"
)


@dataclass
class Position:
    """Ein gehaltenes Papier."""
    kuerzel: str
    stueck: int
    einstand: float          # bezahlter Kurs je Stueck, ohne Gebuehr
    gekauft_am: str
    stop: float              # Kurs, unter dem verkauft wird
    strategie: str = ""


@dataclass
class Auftrag:
    """Eine Entscheidung von gestern, die heute zur Eroeffnung ausgefuehrt wird."""
    kuerzel: str
    aktion: str              # "kaufen" oder "verkaufen"
    grund: str
    erstellt_am: str


@dataclass
class Depot:
    startkapital: float
    bargeld: float
    start_datum: str = ""
    letzter_handelstag: str = ""
    strategie: str = ""
    positionen: dict = field(default_factory=dict)      # kuerzel -> Position
    auftraege: list = field(default_factory=list)       # Auftrag
    historie: list = field(default_factory=list)        # ausgefuehrte Trades
    verlauf: list = field(default_factory=list)         # (datum, depotwert)
    referenz: dict = field(default_factory=dict)        # kuerzel -> Kurs am Start
    gebuehren_gesamt: float = 0.0

    # --- Bewertung --------------------------------------------------------
    def wert(self, kurse):
        """Depotwert = Bargeld + Positionen zum letzten bekannten Kurs."""
        summe = self.bargeld
        for pos in self.positionen.values():
            summe += pos.stueck * kurse.get(pos.kuerzel, pos.einstand)
        return round(summe, 2)

    def frei_fuer_neue_position(self, max_positionen):
        return len(self.positionen) < max_positionen

    # --- Buchungen --------------------------------------------------------
    def kaufen(self, kuerzel, kurs, stueck, datum, gebuehr, grund, stop, strategie):
        kosten = round(stueck * kurs + gebuehr, 2)
        if stueck <= 0 or kosten > self.bargeld + 1e-9:
            return None
        self.bargeld = round(self.bargeld - kosten, 2)
        self.gebuehren_gesamt = round(self.gebuehren_gesamt + gebuehr, 2)
        self.positionen[kuerzel] = Position(kuerzel, stueck, round(kurs, 4), datum,
                                            round(stop, 4), strategie)
        buchung = {
            "datum": datum, "kuerzel": kuerzel, "aktion": "kaufen", "stueck": stueck,
            "kurs": round(kurs, 4), "gebuehr": gebuehr, "wert": kosten, "grund": grund,
        }
        self.historie.append(buchung)
        return buchung

    def verkaufen(self, kuerzel, kurs, datum, gebuehr, grund):
        pos = self.positionen.pop(kuerzel, None)
        if pos is None:
            return None
        erloes = round(pos.stueck * kurs - gebuehr, 2)
        einsatz = pos.stueck * pos.einstand
        self.bargeld = round(self.bargeld + erloes, 2)
        self.gebuehren_gesamt = round(self.gebuehren_gesamt + gebuehr, 2)
        buchung = {
            "datum": datum, "kuerzel": kuerzel, "aktion": "verkaufen", "stueck": pos.stueck,
            "kurs": round(kurs, 4), "gebuehr": gebuehr, "wert": erloes, "grund": grund,
            # Gewinn nach allen Kosten: die Gebuehr des Kaufs steckt nicht im
            # Einstand, also hier beide Seiten abziehen.
            "gewinn": round(erloes - einsatz - gebuehr, 2),
            "gehalten_seit": pos.gekauft_am,
        }
        self.historie.append(buchung)
        return buchung

    # --- Persistenz -------------------------------------------------------
    def als_dict(self):
        daten = asdict(self)
        daten["positionen"] = {k: asdict(p) for k, p in self.positionen.items()}
        daten["auftraege"] = [asdict(a) for a in self.auftraege]
        return daten

    @classmethod
    def aus_dict(cls, daten):
        depot = cls(
            startkapital=daten["startkapital"],
            bargeld=daten["bargeld"],
            start_datum=daten.get("start_datum", ""),
            letzter_handelstag=daten.get("letzter_handelstag", ""),
            strategie=daten.get("strategie", ""),
            historie=daten.get("historie", []),
            verlauf=daten.get("verlauf", []),
            referenz=daten.get("referenz", {}),
            gebuehren_gesamt=daten.get("gebuehren_gesamt", 0.0),
        )
        depot.positionen = {k: Position(**p) for k, p in daten.get("positionen", {}).items()}
        depot.auftraege = [Auftrag(**a) for a in daten.get("auftraege", [])]
        return depot


def laden(pfad=ZUSTAND_PFAD):
    if not os.path.exists(pfad):
        return None
    with open(pfad, encoding="utf-8") as fh:
        return Depot.aus_dict(json.load(fh))


def speichern(depot, pfad=ZUSTAND_PFAD):
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(depot.als_dict(), fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, pfad)
