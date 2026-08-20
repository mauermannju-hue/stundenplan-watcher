#!/usr/bin/env python3
"""Vertretungsplan-Watcher fuer Indiware mobil (BSZ Goerlitz) mit Telegram-Benachrichtigung.

Zwei Betriebsarten:
  (Standard)  Aenderungen der eigenen Klasse melden, sobald sie auftreten.
  --daily     einmal taeglich den kompletten Tagesplan einer zweiten Klasse schicken.

Nur Standardbibliothek - keine Abhaengigkeiten.
"""

import argparse
import base64
import html
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

BASE = "https://www.bszgoerlitz.de/vertret/mobil/mobdaten/"
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "last.json")
LOOKAHEAD_DAYS = 14
TIMEOUT = 30
USER_AGENT = "stundenplan-watcher/1.1 (privater Vertretungsplan-Wecker)"

# Unterrichtszeiten laut bszgoerlitz.de/info/uzeiten.htm (Stand 12.08.2026).
# Alt- und Neubau unterscheiden sich einzig in der 6. Stunde.
ZEITEN = {
    "1": ("07:00", "07:45"),      # nicht fuer Berufsschulklassen
    "2": ("07:55", "08:40"),
    "3": ("08:50", "09:35"),
    "4": ("09:55", "10:40"),
    "5": ("10:50", "11:35"),
    "6": ("11:45", "12:30"),      # Altbau
    "7": ("13:00", "13:45"),
    "8": ("13:55", "14:40"),
    "9": ("14:50", "15:35"),
    "10": ("15:45", "17:15"),
    "11": ("15:45", "17:15"),
    "12": ("17:25", "18:55"),
    "13": ("17:25", "18:55"),
}
ZEITEN_NEUBAU = {"6": ("12:05", "12:50")}


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


# --------------------------------------------------------------------------- Unterrichtszeiten

def gebaeude(raum):
    """A... = Altbau, N... = Neubau, sonst unbekannt."""
    r = (raum or "").strip().upper()
    if r.startswith("N"):
        return "N"
    if r.startswith("A"):
        return "A"
    return ""


def zeit_fuer(st, raum="", hinweis=""):
    """'07:55-08:40'. Bei der 6. Stunde entscheidet das Gebaeude.

    Ist der Raum leer (typisch bei Ausfall), zieht der Gebaeude-Hinweis des Tages;
    bleibt auch der unklar, werden beide Zeiten genannt.
    """
    st = (st or "").strip()
    if st not in ZEITEN:
        return ""
    beginn, ende = ZEITEN[st]
    if st not in ZEITEN_NEUBAU:
        return f"{beginn}–{ende}"
    g = gebaeude(raum) or hinweis
    if g == "N":
        n_beginn, n_ende = ZEITEN_NEUBAU[st]
        return f"{n_beginn}–{n_ende}"
    if g == "A":
        return f"{beginn}–{ende}"
    n_beginn, n_ende = ZEITEN_NEUBAU[st]
    return f"{beginn}–{ende} / Neubau {n_beginn}–{n_ende}"


def gebaeude_hinweis(stunden):
    """Mehrheitliches Gebaeude des Tages - hilft bei Stunden ohne Raumangabe."""
    zaehler = {"A": 0, "N": 0}
    for s in stunden:
        g = gebaeude(s.get("raum"))
        if g in zaehler:
            zaehler[g] += 1
    if zaehler["A"] == zaehler["N"]:
        return ""
    return "A" if zaehler["A"] > zaehler["N"] else "N"


def klingelplan():
    """Kompletter Klingelplan als vorformatierter Block."""
    zeilen = []
    for st in ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "12"):
        beginn, ende = ZEITEN[st]
        label = {"10": "10./11.", "12": "12./13."}.get(st, f"{st}.")
        zeile = f"{label:>7} Std  {beginn}–{ende}"
        if st == "6":
            n_beginn, n_ende = ZEITEN_NEUBAU[st]
            zeile += f"   (Neubau {n_beginn}–{n_ende})"
        if st == "1":
            zeile += "   (nicht fuer Berufsschulklassen)"
        zeilen.append(zeile)
    return "\n".join(zeilen)


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
    gefunden = False
    for kl in root.iter("Kl"):
        if clean(kl.findtext("Kurz")) != klasse:
            continue
        gefunden = True
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
        "gefunden": gefunden,
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


def sortier(st):
    """Stundennummern numerisch sortieren, nicht alphabetisch."""
    try:
        return (0, int(st))
    except (TypeError, ValueError):
        return (1, 0)


def beschreibe(s, hinweis=""):
    """Eine Stunde als lesbare Zeile, mit Unterrichtszeit."""
    zeit = zeit_fuer(s["st"], s.get("raum", ""), hinweis)
    stunde = f"{s['st']}. Std" + (f" ({zeit})" if zeit else "")
    if s["fach"] in ("---", "", "?"):
        text = s["info"] or f"{s['regel'][0]} faellt aus"
        return f"\U0001f534 {stunde}: {text}"
    teile = [s["fach"]]
    if s["lehrer"]:
        teile.append(f"bei {s['lehrer']}")
    if s["raum"]:
        teile.append(f"in {s['raum']}")
    symbol = "\U0001f7e1" if s.get("geaendert") else "\u2022"
    zeile = f"{symbol} {stunde}: {' '.join(teile)}"
    if s["info"]:
        zeile += f" – {s['info']}"
    return zeile


def diff_plan(alt, neu):
    """Liefert eine Liste von Meldungszeilen fuer einen Tag."""
    hinweis = gebaeude_hinweis(neu["stunden"])
    zeilen = []

    if alt is None:
        for s in sorted(neu["stunden"], key=lambda x: sortier(x["st"])):
            zeilen.append(beschreibe(s, hinweis))
        for z in neu["zusatz"]:
            if z:
                zeilen.append(f"ℹ️ {z}")
        return zeilen

    a, b = stunden_index(alt), stunden_index(neu)
    vergleich = ("fach", "lehrer", "raum", "info")
    for key in sorted(b, key=lambda k: sortier(k[0])):
        if key not in a:
            zeilen.append(beschreibe(b[key], hinweis))
        elif any(a[key].get(f) != b[key].get(f) for f in vergleich):
            zeilen.append(beschreibe(b[key], hinweis) + "   (vorher: " + kurz(a[key]) + ")")
    for key in sorted(a, key=lambda k: sortier(k[0])):
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


# --------------------------------------------------------------------------- Betriebsart: Aenderungen

def lauf_aenderungen(args, zugang, klasse, log):
    plaene = sammle_plaene(zugang[0], zugang[1], klasse, log)
    log(f"{len(plaene)} Tagesplan/-plaene gefunden.")

    alt = {}
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as fh:
            alt = json.load(fh).get("plaene", {})

    bloecke = []
    for key in sorted(plaene):
        neu = plaene[key]
        vorher = None if args.force else alt.get(key)
        zeilen = diff_plan(vorher, neu)
        if not zeilen:
            continue
        titel = html.escape(neu["datum"] or key)
        if vorher is None and not args.force:
            titel = f"NEU: {titel}"
        bloecke.append(f"\U0001f4c5 <b>{titel}</b>\n" + "\n".join(html.escape(z) for z in zeilen))

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

    sende(args.tg_token, args.tg_chat, nachricht)
    print(f"Meldung verschickt ({len(bloecke)} Tag(e)).")
    schreibe_state(plaene)


# --------------------------------------------------------------------------- Betriebsart: Tagesplan

def lauf_taeglich(args, zugang, klasse, log):
    """Kompletter Tagesplan einer zweiten Klasse, einmal pro Schultag."""
    plaene = sammle_plaene(zugang[0], zugang[1], klasse, log)
    datiert = {k: v for k, v in plaene.items() if k.isdigit()}
    if not datiert:
        log("Kein datierter Tagesplan verfuegbar - nichts zu senden.")
        return

    heute = date.today().strftime("%Y%m%d")
    kuenftig = sorted(k for k in datiert if k >= heute)
    if not kuenftig:
        log(f"Nur vergangene Plaene vorhanden (neuester {max(datiert)}) - nichts zu senden.")
        return

    key = kuenftig[0]
    plan = datiert[key]
    hinweis = gebaeude_hinweis(plan["stunden"])

    zeilen = []
    for s in sorted(plan["stunden"], key=lambda x: sortier(x["st"])):
        zeilen.append(beschreibe(s, hinweis))
    if not zeilen:
        zeilen.append("\u2014 kein Unterricht im Plan (Klasse steht an diesem Tag nicht drin)"
                      if not plan.get("gefunden")
                      else "\u2014 keine Stunden eingetragen")
    for z in plan["zusatz"]:
        if z:
            zeilen.append(f"ℹ️ {z}")

    nachricht = (
        f"<b>Tagesplan {html.escape(klasse)}</b>\n"
        f"\U0001f4c5 <b>{html.escape(plan['datum'] or key)}</b>\n"
        + "\n".join(html.escape(z) for z in zeilen)
        + "\n\n\U0001f550 <b>Unterrichtszeiten</b>\n<pre>"
        + html.escape(klingelplan())
        + "</pre>"
    )

    if args.dry_run:
        print("\n--- Tagesplan (dry-run) ---\n")
        print(nachricht)
        return

    sende(args.tg_token, args.tg_chat, nachricht)
    print(f"Tagesplan {klasse} fuer {key} verschickt.")


def berliner_stunde():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Berlin")).hour
    except Exception:
        return datetime.now().hour


# --------------------------------------------------------------------------- Main

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Nachricht nur anzeigen, nicht senden")
    ap.add_argument("--init", action="store_true", help="Stand nur speichern, nichts melden")
    ap.add_argument("--force", action="store_true", help="senden, auch ohne Aenderung / ausserhalb des Zeitfensters")
    ap.add_argument("--daily", action="store_true", help="Tagesplan der zweiten Klasse schicken")
    args = ap.parse_args()

    user = os.environ.get("PLAN_USER")
    password = os.environ.get("PLAN_PASS")
    klasse = os.environ.get("PLAN_KLASSE", "FOS25W")
    klasse2 = os.environ.get("PLAN_KLASSE2", "SOZ25R1")
    args.tg_token = os.environ.get("TG_TOKEN")
    args.tg_chat = os.environ.get("TG_CHAT")
    daily_hour = int(os.environ.get("DAILY_HOUR", "6"))

    if not user or not password:
        raise SystemExit("FEHLER: PLAN_USER und PLAN_PASS muessen gesetzt sein.")
    if not (args.dry_run or args.init) and not (args.tg_token and args.tg_chat):
        raise SystemExit("FEHLER: TG_TOKEN und TG_CHAT muessen gesetzt sein.")

    log = print if (args.dry_run or os.environ.get("VERBOSE")) else (lambda *_: None)

    if args.daily:
        stunde = berliner_stunde()
        if not (args.force or args.dry_run) and stunde != daily_hour:
            print(f"Nicht im Zeitfenster (Berlin {stunde}:xx, gewuenscht {daily_hour}:xx) - uebersprungen.")
            return
        log(f"Tagesplan {klasse2}, Abruf laeuft ...")
        lauf_taeglich(args, (user, password), klasse2, log)
        return

    log(f"Klasse {klasse}, Abruf laeuft ...")
    lauf_aenderungen(args, (user, password), klasse, log)


def schreibe_state(plaene):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"plaene": plaene}, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, STATE_PATH)


if __name__ == "__main__":
    main()
