#!/usr/bin/env python3
"""Kursdaten holen und lokal vorhalten.

Zwei Quellen, weil beide kostenlos und beide gelegentlich launisch sind:
  stooq.com  - liefert CSV, komplette Historie, kein Schluessel noetig
  Yahoo      - liefert JSON, gleicher Umfang, anderer Ausfallzeitpunkt

Geholte Kurse landen als CSV unter state/kurse/<KUERZEL>.csv. Das spart
Abrufe, macht Laeufe nachvollziehbar und erlaubt Backtests ohne Netz.

Nur Standardbibliothek - keine Abhaengigkeiten.
"""

import csv
import io
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from .konfig import TIMEOUT, USER_AGENT

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "kurse"
)
SPALTEN = ["datum", "auf", "hoch", "tief", "schluss", "volumen"]


@dataclass(frozen=True)
class Kerze:
    """Ein Handelstag: Eroeffnung, Hoch, Tief, Schluss, Umsatz."""
    datum: str        # ISO, also YYYY-MM-DD
    auf: float
    hoch: float
    tief: float
    schluss: float
    volumen: float

    def als_zeile(self):
        return [self.datum, self.auf, self.hoch, self.tief, self.schluss, self.volumen]


def _plausibel(kerze):
    """Offensichtlich kaputte Zeilen aussortieren, statt sie zu handeln."""
    werte = [kerze.auf, kerze.hoch, kerze.tief, kerze.schluss]
    if any(w is None or w <= 0 for w in werte):
        return False
    return kerze.tief <= min(kerze.auf, kerze.schluss) and kerze.hoch >= max(kerze.auf, kerze.schluss)


def _abrufen(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def von_stooq(wert):
    """Komplette Tageshistorie als CSV: Date,Open,High,Low,Close,Volume."""
    roh = _abrufen(f"https://stooq.com/q/d/l/?s={wert.stooq}&i=d").decode("utf-8", "replace")
    if not roh.lower().startswith("date"):
        raise RuntimeError(f"stooq lieferte keine Kurstabelle fuer {wert.stooq}: {roh[:80]!r}")
    kerzen = []
    for zeile in csv.DictReader(io.StringIO(roh)):
        try:
            kerze = Kerze(
                zeile["Date"],
                float(zeile["Open"]), float(zeile["High"]),
                float(zeile["Low"]), float(zeile["Close"]),
                float(zeile.get("Volume") or 0),
            )
        except (TypeError, ValueError):
            continue          # stooq schreibt "N/D" in Luecken
        if _plausibel(kerze):
            kerzen.append(kerze)
    return kerzen


def von_yahoo(wert, tage):
    """Yahoo-Chart-API. Bereich grosszuegig waehlen, wir schneiden selbst zu."""
    spanne = "2y" if tage <= 500 else "5y"
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{wert.yahoo}"
           f"?range={spanne}&interval=1d")
    daten = json.loads(_abrufen(url))
    ergebnis = (daten.get("chart") or {}).get("result") or []
    if not ergebnis:
        fehler = (daten.get("chart") or {}).get("error")
        raise RuntimeError(f"Yahoo kennt {wert.yahoo} nicht: {fehler}")
    block = ergebnis[0]
    zeiten = block.get("timestamp") or []
    kurse = (block.get("indicators") or {}).get("quote") or [{}]
    kurse = kurse[0]
    kerzen = []
    for i, stempel in enumerate(zeiten):
        try:
            kerze = Kerze(
                datetime.fromtimestamp(stempel, timezone.utc).date().isoformat(),
                float(kurse["open"][i]), float(kurse["high"][i]),
                float(kurse["low"][i]), float(kurse["close"][i]),
                float(kurse["volume"][i] or 0),
            )
        except (TypeError, ValueError, KeyError, IndexError):
            continue          # Yahoo setzt null in Handelspausen
        if _plausibel(kerze):
            kerzen.append(kerze)
    return kerzen


# --------------------------------------------------------------------------- Cache

def cache_pfad(wert):
    return os.path.join(CACHE_DIR, f"{wert.kuerzel}.csv")


def lade_cache(wert):
    pfad = cache_pfad(wert)
    if not os.path.exists(pfad):
        return []
    kerzen = []
    with open(pfad, newline="", encoding="utf-8") as fh:
        for zeile in csv.DictReader(fh):
            try:
                kerzen.append(Kerze(
                    zeile["datum"], float(zeile["auf"]), float(zeile["hoch"]),
                    float(zeile["tief"]), float(zeile["schluss"]), float(zeile["volumen"]),
                ))
            except (TypeError, ValueError, KeyError):
                continue
    return sorted(kerzen, key=lambda k: k.datum)


def speichere_cache(wert, kerzen):
    os.makedirs(CACHE_DIR, exist_ok=True)
    pfad = cache_pfad(wert)
    tmp = pfad + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        schreiber = csv.writer(fh)
        schreiber.writerow(SPALTEN)
        for kerze in kerzen:
            schreiber.writerow(kerze.als_zeile())
    os.replace(tmp, pfad)


def vereinigen(alt, neu):
    """Neue Kurse gewinnen, alte bleiben erhalten - nach Datum sortiert."""
    nach_datum = {k.datum: k for k in alt}
    nach_datum.update({k.datum: k for k in neu})
    return [nach_datum[d] for d in sorted(nach_datum)]


def hole(wert, tage, quelle="auto", nur_cache=False, log=lambda *_: None):
    """Historie eines Wertes besorgen: Cache lesen, ergaenzen, zurueckschreiben."""
    zwischenstand = lade_cache(wert)
    if nur_cache:
        return zwischenstand[-tage:]

    fehler = []
    reihenfolge = {"stooq": ["stooq"], "yahoo": ["yahoo"]}.get(quelle, ["stooq", "yahoo"])
    for name in reihenfolge:
        try:
            frisch = von_stooq(wert) if name == "stooq" else von_yahoo(wert, tage)
            if not frisch:
                raise RuntimeError("leere Antwort")
            zwischenstand = vereinigen(zwischenstand, frisch)
            speichere_cache(wert, zwischenstand)
            log(f"  {wert.kuerzel}: {len(frisch)} Kurse von {name}, "
                f"neuester {frisch[-1].datum}")
            return zwischenstand[-tage:]
        except (urllib.error.URLError, RuntimeError, ValueError, OSError) as exc:
            fehler.append(f"{name}: {exc}")

    if zwischenstand:
        log(f"  {wert.kuerzel}: Abruf fehlgeschlagen ({'; '.join(fehler)}) - nutze Cache "
            f"bis {zwischenstand[-1].datum}")
        return zwischenstand[-tage:]
    raise RuntimeError(f"Keine Kurse fuer {wert.kuerzel} - {'; '.join(fehler)}")


def hole_alle(werte, tage, quelle="auto", nur_cache=False, log=lambda *_: None):
    """Watchlist abarbeiten. Ein kaputtes Papier legt nicht den Lauf lahm."""
    daten, ausgefallen = {}, []
    for wert in werte:
        try:
            kerzen = hole(wert, tage, quelle, nur_cache, log)
            if kerzen:
                daten[wert.kuerzel] = kerzen
            else:
                ausgefallen.append(wert.kuerzel)
        except RuntimeError as exc:
            log(f"  {wert.kuerzel}: {exc}")
            ausgefallen.append(wert.kuerzel)
    return daten, ausgefallen
