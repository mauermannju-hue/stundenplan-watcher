#!/usr/bin/env python3
"""Vertretungsplan-Watcher fuer Indiware mobil (BSZ Goerlitz) mit Telegram-Benachrichtigung.

Holt die Tagesplaene als XML, filtert auf eine Klasse, vergleicht mit dem
zuletzt gesehenen Stand und meldet nur echte Aenderungen per Telegram.

Nur Standardbibliothek - keine Abhaengigkeiten.
"""

import argparse
import base64
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta

BASE = "https://www.bszgoerlitz.de/vertret/mobil/mobdaten/"
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "last.json")
LOOKAHEAD_DAYS = 14
TIMEOUT = 30
USER_AGENT = "stundenplan-watcher/1.0 (privater Vertretungsplan-Wecker)"


# --------------------------------------------------------------------------- HTTP

def fetch(url, user, password, retries=3):
    """Gibt die Bytes zurueck, None bei 404, wirft bei allem anderen."""
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {token}",
        "User-Agent": USER_AGENT,
    })
    last_error = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code == 401:
                raise SystemExit("FEHLER: Basic-Auth abgelehnt (401). Zugangsdaten pruefen.")
            last_error = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        if attempt < retries - 1:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Abruf von {url} fehlgeschlagen: {last_error}")


# --------------------------------------------------------------------------- Parsing

def clean(text):
    """Indiware schreibt Leerfelder als &nbsp; - das soll leer sein."""
    if text is None:
        return ""
    return text.replace("&nbsp;", "").replace("\xa0", " ").strip()


def parse_plan(raw, klasse):
    """Extrahiert Kopfdaten, die Stunden der Klasse und die Zusatzinfos."""
    root = ET.fromstring(raw)
    kopf = root.find("Kopf")

    stunden = []
    for kl in root.iter("Kl"):
        if clean(kl.findtext("Kurz")) != klasse:
            continue
        # Unterrichtsnummer -> (Fach, Lehrer) laut Regelstundenplan
        regel = {}
        for ue in kl.iter("UeNr"):
            regel[clean(ue.text)] = (ue.get("UeFa", ""), ue.get("UeLe", ""))
        for std in kl.iter("Std"):
            fa = std.find("Fa")
            le = std.find("Le")
            ra = std.find("Ra")
            nr = clean(std.findtext("Nr"))
            stunden.append({
                "st": clean(std.findtext("St")),
                "nr": nr,
                "fach": clean(fa.text if fa is not None else ""),
                "lehrer": clean(le.text if le is not None else ""),
                "raum": clean(ra.text if ra is not None else ""),
                "info": clean(std.findtext("If")),
                "geaendert": sorted(filter(None, [
                    "Fach" if fa is not None and fa.get("FaAe") else "",
                    "Lehrer" if le is not None and le.get("LeAe") else "",
                    "Raum" if ra is not None and ra.get("RaAe") else "",
                ])),
                "regel": list(regel.get(nr, ("", ""))),
            })

    zusatz = [clean(z.text) for z in root.iter("ZiZeile")]
    while zusatz and not zusatz[-1]:
        zusatz.pop()

    return {
        "datum": clean(kopf.findtext("DatumPlan")) if kopf is not None else "",
        # zeitstempel bewusst NICHT Teil des Vergleichs - aendert sich bei jedem Upload
        "zeitstempel": clean(kopf.findtext("zeitstempel")) if kopf is not None else "",
        "stunden": stunden,
        "zusatz": zusatz,
    }


def freie_tage(raw):
    """Liefert die schulfreien Tage als Menge im Format JJMMTT."""
    try:
        return {clean(ft.text) for ft in ET.fromstring(raw).iter("ft")}
    except ET.ParseError:
        return set()


def sammle_plaene(user, password, klasse, log=lambda *_: None):
    """Holt Klassen.xml (= aktuellster Tag) plus alle kuenftigen Tagesdateien."""
    plaene = {}

    aktuell = fetch(BASE + "Klassen.xml", user, password)
    frei = set()
    if aktuell:
        frei = freie_tage(aktuell)
        datei = ""
        kopf = ET.fromstring(aktuell).find("Kopf")
        if kopf is not None:
            datei = clean(kopf.findtext("datei"))
        key = datei.replace("PlanKl", "").replace(".xml", "") or "aktuell"
        plaene[key] = parse_plan(aktuell, klasse)
        log(f"  Klassen.xml -> {key} ({plaene[key]['datum']})")

    heute = date.today()
    for offset in range(LOOKAHEAD_DAYS + 1):
        tag = heute + timedelta(days=offset)
        if tag.weekday() >= 5:                       # Wochenende
            continue
        if tag.strftime("%y%m%d") in frei:           # Ferien / Feiertag
            continue
        key = tag.strftime("%Y%m%d")
        if key in plaene:
            continue
        raw = fetch(f"{BASE}PlanKl{key}.xml", user, password)
        if raw:
            plaene[key] = parse_plan(raw, klasse)
            log(f"  PlanKl{key}.xml -> {plaene[key]['datum']}")

    return plaene


# --------------------------------------------------------------------------- Diff

def stunden_index(plan):
    return {(s["st"], s["nr"]): s for s in plan["stunden"]}


def beschreibe(s):
    """Eine Stunde als lesbare Zeile."""
    stunde = f"{s['st']}. Std"
    if s["fach"] in ("---", "", "?"):
        text = s["info"] or f"{s['regel'][0]} faellt aus"
        return f"\U0001f534 {stunde}: {text}"
    teile = [s["fach"]]
    if s["lehrer"]:
        teile.append(f"bei {s['lehrer']}")
    if s["raum"]:
        teile.append(f"in {s['raum']}")
    zeile = f"\U0001f7e1 {stunde}: {' '.join(teile)}"
    if s["info"]:
        zeile += f" – {s['info']}"
    return zeile


def diff_plan(alt, neu):
    """Liefert eine Liste von Meldungszeilen fuer einen Tag."""
    zeilen = []

    if alt is None:
        for s in sorted(neu["stunden"], key=lambda x: (len(x["st"]), x["st"])):
            zeilen.append(beschreibe(s))
        for z in neu["zusatz"]:
            if z:
                zeilen.append(f"ℹ️ {z}")
        return zeilen

    a, b = stunden_index(alt), stunden_index(neu)
    for key in sorted(b, key=lambda k: (len(k[0]), k[0])):
        vergleich = ("fach", "lehrer", "raum", "info")
        if key not in a:
            zeilen.append(beschreibe(b[key]))
        elif any(a[key].get(f) != b[key].get(f) for f in vergleich):
            zeilen.append(beschreibe(b[key]) + "   (vorher: " + kurz(a[key]) + ")")
    for key in sorted(a, key=lambda k: (len(k[0]), k[0])):
        if key not in b:
            zeilen.append(f"✅ {a[key]['st']}. Std: Aenderung aufgehoben ({kurz(a[key])})")

    alt_z, neu_z = [z for z in alt["zusatz"] if z], [z for z in neu["zusatz"] if z]
    for z in neu_z:
        if z not in alt_z:
            zeilen.append(f"ℹ️ neu: {z}")
    for z in alt_z:
        if z not in neu_z:
            zeilen.append(f"ℹ️ entfernt: {z}")

    return zeilen


def kurz(s):
    if s["fach"] in ("---", "", "?"):
        return s["info"] or "Ausfall"
    return " ".join(filter(None, [s["fach"], s["lehrer"], s["raum"]]))


# --------------------------------------------------------------------------- Telegram

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


# --------------------------------------------------------------------------- Main

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Nachricht nur anzeigen, nicht senden")
    ap.add_argument("--init", action="store_true", help="Stand nur speichern, nichts melden")
    ap.add_argument("--force", action="store_true", help="Kompletten Plan senden, auch ohne Aenderung")
    args = ap.parse_args()

    user = os.environ.get("PLAN_USER")
    password = os.environ.get("PLAN_PASS")
    klasse = os.environ.get("PLAN_KLASSE", "FOS25W")
    tg_token = os.environ.get("TG_TOKEN")
    tg_chat = os.environ.get("TG_CHAT")

    if not user or not password:
        raise SystemExit("FEHLER: PLAN_USER und PLAN_PASS muessen gesetzt sein.")
    if not (args.dry_run or args.init) and not (tg_token and tg_chat):
        raise SystemExit("FEHLER: TG_TOKEN und TG_CHAT muessen gesetzt sein.")

    log = print if (args.dry_run or os.environ.get("VERBOSE")) else (lambda *_: None)
    log(f"Klasse {klasse}, Abruf laeuft ...")
    plaene = sammle_plaene(user, password, klasse, log)
    log(f"{len(plaene)} Tagesplan/-plaene gefunden.")

    alt = {}
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as fh:
            alt = json.load(fh).get("plaene", {})

    bloecke = []
    for key in sorted(plaene):
        neu = plaene[key]
        vorher = alt.get(key)
        if args.force:
            zeilen = diff_plan(None, neu)
        else:
            zeilen = diff_plan(vorher, neu)
        if not zeilen:
            continue
        kopf = html.escape(neu["datum"] or key)
        if vorher is None and not args.force:
            kopf = f"\U0001f4c5 <b>NEU: {kopf}</b>"
        else:
            kopf = f"\U0001f4c5 <b>{kopf}</b>"
        bloecke.append(kopf + "\n" + "\n".join(html.escape(z) for z in zeilen))

    # Tage, die aus dem Plan verschwunden sind, still verwerfen (vergangene Tage)

    if args.init:
        schreibe_state(plaene)
        print(f"Stand gespeichert ({len(plaene)} Tage) - keine Meldung verschickt.")
        return

    if not bloecke:
        log("Keine Aenderung.")
        schreibe_state(plaene)
        return

    nachricht = f"<b>Vertretungsplan {html.escape(klasse)}</b>\n\n" + "\n\n".join(bloecke)

    if args.dry_run:
        print("\n--- Nachricht (dry-run) ---\n")
        print(nachricht)
        return

    sende(tg_token, tg_chat, nachricht)
    print(f"Meldung verschickt ({len(bloecke)} Tag(e)).")
    schreibe_state(plaene)


def schreibe_state(plaene):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"plaene": plaene}, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, STATE_PATH)


if __name__ == "__main__":
    main()
