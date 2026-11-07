"""Kommandozeile:  python -m inspo <befehl>

  bot                      Telegram-Bot starten (Dauerbetrieb, z.B. als systemd-Dienst auf dem Tower)
  add <url> [--notiz ..]   einzelnen Link aufnehmen und sofort analysieren
  idee "<text>"            Idee einschaetzen lassen
  import <export>          WhatsApp-Export (.txt) oder Telegram-Export (result.json) einlesen
  verarbeite [--limit N]   Warteschlange abarbeiten (z.B. nach einem Import)
  vorschlag "<briefing>"    Bibliothek auf ein neues Projekt anwenden (Stil-Varianten)
  bibliothek               STIL-BIBLIOTHEK.md neu schreiben
  skill <ordner>           Bibliothek als Claude-Skill exportieren (fuer den KI-Mitarbeiter)
  suche <wort> / liste     Gesammeltes durchsuchen / auflisten
"""
import argparse
import logging
import sys

from . import bibliothek, ingest
from .db import DB
from .verarbeitung import arbeite_warteschlange, verarbeite


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m inspo", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="befehl", required=True)
    sub.add_parser("bot")
    a = sub.add_parser("add")
    a.add_argument("url")
    a.add_argument("--notiz")
    a.add_argument("--web", action="store_true", help="als Website behandeln")
    a.add_argument("--erzwingen", action="store_true", help="Vorfilter ueberspringen")
    i = sub.add_parser("idee")
    i.add_argument("text", nargs="+")
    im = sub.add_parser("import")
    im.add_argument("datei")
    im.add_argument("--seit", help="nur Nachrichten ab Datum JJJJ-MM-TT")
    im.add_argument("--sofort", action="store_true", help="direkt im Anschluss verarbeiten")
    v = sub.add_parser("verarbeite")
    v.add_argument("--limit", type=int)
    vs = sub.add_parser("vorschlag")
    vs.add_argument("briefing", nargs="+")
    sub.add_parser("bibliothek")
    s = sub.add_parser("skill")
    s.add_argument("ordner")
    su = sub.add_parser("suche")
    su.add_argument("wort")
    sub.add_parser("liste")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.befehl == "bot":
        from .bot import main as bot_main
        return bot_main()

    db = DB()
    if args.befehl == "add":
        url = ingest.normalisiere(args.url)
        art = "website" if args.web else ingest.art_von(url)
        eid, neu = db.neuer_eintrag(art=art, quelle="cli", url=url, notiz=args.notiz)
        if not neu and not args.erzwingen:
            print(f"#{eid} ist schon bekannt (Status {db.eintrag(eid)['status']}). --erzwingen fuer neue Analyse.")
            return
        _zeige(verarbeite(db, eid, erzwingen=args.erzwingen))
    elif args.befehl == "idee":
        eid, _ = db.neuer_eintrag(art="idee", quelle="cli", notiz=" ".join(args.text))
        _zeige(verarbeite(db, eid))
    elif args.befehl == "import":
        nachrichten = ingest.lese_export(args.datei)
        neu = doppelt = 0
        for m in nachrichten:
            if args.seit and (m["zeit"] or "") < args.seit:
                continue
            notiz = ingest.text_ohne_urls(m["text"]) or None
            for url in ingest.finde_urls(m["text"]):
                _, ist_neu = db.neuer_eintrag(art=ingest.art_von(url), quelle="import", url=url, notiz=notiz,
                                              absender=m["absender"], erstellt=m["zeit"])
                neu += ist_neu
                doppelt += not ist_neu
        print(f"{len(nachrichten)} Nachrichten gelesen: {neu} neue Links, {doppelt} doppelte.")
        if args.sofort:
            print(f"{arbeite_warteschlange(db, rueckmeldung=lambda e, r: print(_kurz(e, r)))} verarbeitet.")
    elif args.befehl == "verarbeite":
        n = arbeite_warteschlange(db, args.limit, rueckmeldung=lambda e, r: print(_kurz(e, r)))
        print(f"{n} Eintraege verarbeitet.")
    elif args.befehl == "vorschlag":
        from . import analyse
        datei = bibliothek.schreibe_bibliothek(db)
        print(analyse.vorschlag(briefing=" ".join(args.briefing), bibliothek_voll=datei.read_text(encoding="utf-8")))
    elif args.befehl == "bibliothek":
        print(bibliothek.schreibe_bibliothek(db))
    elif args.befehl == "skill":
        print(bibliothek.exportiere_skill(db, args.ordner))
    elif args.befehl in ("suche", "liste"):
        zeilen = db.suche(args.wort, limit=50) if args.befehl == "suche" else \
            db.con.execute("SELECT * FROM eintraege ORDER BY id DESC LIMIT 50").fetchall()
        for e in zeilen:
            print(f"#{e['id']:>4} {e['status']:<10} {e['score'] if e['score'] is not None else '-':>2}/10  "
                  f"{e['art']:<8} {(e['titel'] or e['url'] or e['notiz'] or '')[:70]}")


def _kurz(e, r):
    return f"#{e['id']} {r.get('status')} {r.get('score', '')} {r.get('titel') or e['url'] or ''}"


def _zeige(ergebnis):
    import re
    text = re.sub(r"<[^>]+>", "", ergebnis.get("text", "")).replace("&lt;", "<").replace("&gt;", ">") \
        .replace("&amp;", "&").replace("&quot;", '"').replace("&#x27;", "'")
    print(text)
    if ergebnis.get("markdown"):
        print(f"\nBreakdown: {ergebnis['markdown']}")


if __name__ == "__main__":
    sys.exit(main())
