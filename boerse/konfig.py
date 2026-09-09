#!/usr/bin/env python3
"""Alle Stellschrauben des Boersen-Simulators an einem Ort.

Wer etwas veraendern will, aendert es hier - nicht im Code der Strategien.
Ueber Umgebungsvariablen laesst sich jeder Wert im Workflow ueberschreiben,
ohne die Datei anzufassen.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Wert:
    """Ein handelbares Papier mit den Kuerzeln beider Kursquellen."""
    kuerzel: str          # interner Name, taucht in Zustand und Bericht auf
    name: str             # Klartext fuer den Bericht
    stooq: str            # Symbol bei stooq.com
    yahoo: str            # Symbol bei Yahoo Finance


# Standard-Watchlist: sechs liquide DAX-Werte mit unterschiedlichem Charakter.
# Bewusst alle in Euro an einem Handelsplatz - so gibt es kein Waehrungsrisiko,
# das die Auswertung verfaelschen wuerde. US-Werte lassen sich ergaenzen
# (z. B. Wert("AAPL", "Apple", "aapl.us", "AAPL")), dann rechnet der Simulator
# aber Dollar und Euro faelschlich als eine Waehrung - siehe BOERSE.md.
WATCHLIST = [
    Wert("SAP", "SAP",               "sap.de", "SAP.DE"),
    Wert("SIE", "Siemens",           "sie.de", "SIE.DE"),
    Wert("ALV", "Allianz",           "alv.de", "ALV.DE"),
    Wert("BMW", "BMW",               "bmw.de", "BMW.DE"),
    Wert("DTE", "Deutsche Telekom",  "dte.de", "DTE.DE"),
    Wert("RHM", "Rheinmetall",       "rhm.de", "RHM.DE"),
]


def _zahl(name, standard):
    """Umgebungsvariable als Zahl lesen, sonst den Standardwert nehmen."""
    roh = os.environ.get(name, "").strip()
    if not roh:
        return standard
    try:
        return type(standard)(roh)
    except ValueError:
        raise SystemExit(f"{name}={roh!r} ist keine gueltige Zahl")


# --- Kapital und Kosten ----------------------------------------------------
# STARTKAPITAL bewusst auf den Betrag setzen, den man spaeter wirklich
# einsetzen wuerde. Genau daran zeigt sich, ob die Gebuehren die Strategie
# auffressen - bei 200 Euro Depot ist 1 Euro pro Order eine halbe Prozent.
STARTKAPITAL = _zahl("BOERSE_STARTKAPITAL", 1000.0)
GEBUEHR_JE_ORDER = _zahl("BOERSE_GEBUEHR", 1.0)      # Neobroker-Niveau
SLIPPAGE = _zahl("BOERSE_SLIPPAGE", 0.001)           # 0,1 % Spread je Seite

# --- Risikoregeln ----------------------------------------------------------
MAX_POSITIONEN = _zahl("BOERSE_MAX_POSITIONEN", 3)
MAX_ANTEIL_JE_POSITION = _zahl("BOERSE_MAX_ANTEIL", 0.33)
MIN_ORDERVOLUMEN = _zahl("BOERSE_MIN_ORDER", 150.0)  # darunter frisst die Gebuehr zu viel
STOP_PROZENT = _zahl("BOERSE_STOP", 0.06)            # 6 % unter Einstand

# --- Strategie -------------------------------------------------------------
# Gueltige Namen stehen in strategien.KATALOG.
STRATEGIE = os.environ.get("BOERSE_STRATEGIE", "sma").strip() or "sma"

# --- Technisches -----------------------------------------------------------
HISTORIE_TAGE = _zahl("BOERSE_HISTORIE", 400)        # Vorlauf fuer die Indikatoren
# Wie viele vergangene Tage die Strategie je Entscheidung sieht. Muss deutlich
# groesser sein als der laengste Indikator (SMA 50), sonst schweigt sie. Nach
# oben begrenzt, damit Backtest und Livebetrieb dieselben Werte berechnen -
# der RSI nach Wilder haengt sonst von der Laenge der Historie ab.
INDIKATOR_FENSTER = _zahl("BOERSE_FENSTER", 250)
QUELLE = os.environ.get("BOERSE_QUELLE", "auto").strip() or "auto"
TIMEOUT = 30
USER_AGENT = "boersen-simulator/1.0 (privates Lernprojekt)"
