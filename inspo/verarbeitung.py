"""Pipeline je Eintrag: holen -> messen -> Vorfilter -> Tiefenanalyse -> Bibliothek."""
import html
import logging
import traceback
from pathlib import Path

from . import analyse, bibliothek, config, video
from .db import DB

log = logging.getLogger("inspo")


class Ergebnis(dict):
    """status, urteil, score, titel, text (Telegram-HTML), markdown"""


def verarbeite(db: DB, eintrag_id: int, erzwingen: bool = False) -> Ergebnis:
    """Verarbeitet einen Eintrag vollstaendig. Fehler landen im Eintrag (status='fehler'),
    damit ein kaputter Link die Warteschlange nicht blockiert."""
    e = db.eintrag(eintrag_id)
    db.aktualisiere(eintrag_id, status="in_arbeit", fehler=None)
    try:
        if e["art"] == "video":
            return _video(db, e, erzwingen)
        if e["art"] == "website":
            return _website(db, e)
        return _idee(db, e)
    except Exception as exc:  # noqa: BLE001 - alles abfangen, Warteschlange muss weiterlaufen
        log.error("Eintrag %s fehlgeschlagen: %s\n%s", eintrag_id, exc, traceback.format_exc())
        db.aktualisiere(eintrag_id, status="fehler", fehler=str(exc)[:1000])
        return Ergebnis(status="fehler", text=f"⚠️ #{eintrag_id} ging schief: {html.escape(str(exc)[:300])}")


# --------------------------------------------------------------------------- Video

def _video(db: DB, e, erzwingen: bool) -> Ergebnis:
    eid = e["id"]
    ordner = config.MEDIEN / f"{eid:04d}"
    if e["datei"]:
        pfad, info = Path(e["datei"]), {}
    else:
        pfad, info = video.lade(e["url"], ordner)

    meta = video.metadaten(pfad)
    cuts = video.schnitte(pfad)
    meta.update(video.schnitt_statistik(meta["dauer"], cuts))
    meta.update(info)
    meta["schnitt_zeitpunkte"] = cuts[:60]
    db.aktualisiere(eid, meta_json=meta)
    kurz = bibliothek.kurzfassung(db)

    # 1) Vorfilter mit wenigen Bildern
    tb = video.bilder(pfad, video.zeitpunkte(meta["dauer"], cuts, config.BILDER_TRIAGE), ordner / "bilder")
    t = analyse.triage(url=e["url"], notiz=e["notiz"], meta=_meta_fuer_claude(meta),
                       bilder=[(f"Bild bei {zeit:.1f}s", p) for zeit, p in tb], bibliothek_kurz=kurz)
    db.aktualisiere(eid, triage_score=t.relevanz, triage_grund=t.begruendung, bereich=t.bereich,
                    titel=t.kurzbeschreibung)
    zu_lang = meta["dauer"] > config.MAX_VIDEO_SEKUNDEN
    if not erzwingen and (t.relevanz < config.SCHWELLE or zu_lang):
        grund = "Longform – nur vorgefiltert" if zu_lang else t.begruendung
        db.aktualisiere(eid, status="verworfen", urteil="verwerfen", score=t.relevanz, zusammenfassung=grund)
        video.aufraeumen(ordner, video_behalten=False)
        return Ergebnis(status="verworfen", urteil="verwerfen", score=t.relevanz, titel=t.kurzbeschreibung,
                        text=f"🥱 <b>#{eid}</b> {html.escape(t.kurzbeschreibung)}\n"
                             f"Relevanz {t.relevanz}/10 – {html.escape(grund)}\n"
                             f"<i>/analyse {eid} erzwingt den vollen Breakdown.</i>")

    # 2) Tiefenanalyse mit vielen Bildern + Transkript
    ab = video.bilder(pfad, video.zeitpunkte(meta["dauer"], cuts, config.BILDER_ANALYSE), ordner / "bilder")
    beschriftet = [(f"Bild bei {zeit:.1f}s" + (" (Hook)" if i == 0 else ""), p) for i, (zeit, p) in enumerate(ab)]
    text = video.transkript(pfad) if meta.get("hat_ton") else None
    b = analyse.video_breakdown(url=e["url"], notiz=e["notiz"], meta=_meta_fuer_claude(meta), bilder=beschriftet,
                                transkript=text, bibliothek_kurz=kurz)
    return _abschliessen(db, eid, b.model_dump(), "videografie", beschriftet, ordner)


def _meta_fuer_claude(meta: dict) -> dict:
    """Nur das, was fuer die Bewertung zaehlt - keine Rohlisten."""
    schluessel = ("dauer", "format", "fps", "hat_ton", "schnitte", "schnitte_pro_sekunde",
                  "mittlere_einstellungslaenge", "erster_schnitt", "title", "description", "uploader",
                  "channel", "view_count", "like_count", "comment_count", "upload_date", "extractor_key")
    return {k: meta[k] for k in schluessel if k in meta}


# --------------------------------------------------------------------------- Website

def _website(db: DB, e) -> Ergebnis:
    from . import web
    eid = e["id"]
    ordner = config.MEDIEN / f"{eid:04d}"
    fakten, bilder = web.erfasse(e["url"], ordner / "bilder")
    db.aktualisiere(eid, meta_json=fakten)
    b = analyse.web_breakdown(url=e["url"], notiz=e["notiz"], fakten=fakten, bilder=bilder,
                              bibliothek_kurz=bibliothek.kurzfassung(db))
    return _abschliessen(db, eid, b.model_dump(), "web", bilder, ordner)


# --------------------------------------------------------------------------- Idee

def _idee(db: DB, e) -> Ergebnis:
    eid = e["id"]
    a = analyse.idee(text=e["notiz"] or "", absender=e["absender"], bibliothek_kurz=bibliothek.kurzfassung(db))
    d = a.model_dump()
    db.aktualisiere(eid, status="fertig", bereich=a.bereich, titel=a.titel, zusammenfassung=a.einschaetzung,
                    tags=a.tags, analyse_json=d, urteil="teilweise")
    md = bibliothek.schreibe_eintrag(db, eid)
    db.aktualisiere(eid, markdown=str(md))
    schritte = "\n".join(f"• {html.escape(s)}" for s in a.naechste_schritte[:4])
    moegl = "\n".join(f"• {html.escape(s)}" for s in a.moeglichkeiten[:4])
    return Ergebnis(status="fertig", titel=a.titel, markdown=str(md), text=(
        f"💡 <b>#{eid} {html.escape(a.titel)}</b> ({a.bereich})\n{html.escape(a.einschaetzung)}\n\n"
        f"<b>Moeglichkeiten</b>\n{moegl}\n\n<b>Naechste Schritte</b>\n{schritte}\n\n"
        f"Aufwand {a.aufwand} · Nutzen {a.nutzen}"))


# --------------------------------------------------------------------------- gemeinsam

def _abschliessen(db: DB, eid: int, d: dict, bereich_bib: str, bilder, ordner: Path) -> Ergebnis:
    db.aktualisiere(eid, status="fertig", bereich=d["bereich"], titel=d["titel"], score=d["score"],
                    urteil=d["urteil"], zusammenfassung=d["zusammenfassung"], tags=d["tags"], analyse_json=d)
    bibliothek.merke(db, eid, d, bereich_bib)
    md = bibliothek.schreibe_eintrag(db, eid, bilder)
    db.aktualisiere(eid, markdown=str(md))
    bibliothek.schreibe_bibliothek(db)
    video.aufraeumen(ordner, video_behalten=config.VIDEOS_BEHALTEN and d["urteil"] != "verwerfen")
    return Ergebnis(status="fertig", urteil=d["urteil"], score=d["score"], titel=d["titel"], markdown=str(md),
                    text=telegram_zusammenfassung(eid, d))


def telegram_zusammenfassung(eid: int, d: dict) -> str:
    e = html.escape
    zeilen = [f"{bibliothek.URTEIL_EMOJI.get(d['urteil'], '')} <b>#{eid} {e(d['titel'])}</b> – {d['score']}/10",
              e(d["zusammenfassung"])]
    if d.get("hook"):
        zeilen.append(f"\n<b>Hook:</b> {e(d['hook'])}")
    cap = d.get("captions") or {}
    if cap.get("vorhanden"):
        zeilen.append(f"<b>Captions:</b> {e(cap['schrift_vermutung'])}, {e(cap['gewicht_und_schreibweise'])}, "
                      f"{e(cap['animation'])}")
    gut = [t for t in d.get("techniken", []) if t["bewertung"] == "gut"][:4]
    if gut:
        zeilen.append("\n<b>Gemerkt:</b>\n" + "\n".join(f"✅ {e(t['name'])}" for t in gut))
    schlecht = [t for t in d.get("techniken", []) if t["bewertung"] == "schlecht"][:2]
    if schlecht:
        zeilen.append("\n".join(f"⛔ {e(t['name'])}" for t in schlecht))
    zeilen.append(f"\n<b>Fuer uns:</b> {e(d['anwendung_fuer_uns'])}")
    zeilen.append(f"\n<i>/details {eid} – kompletter Breakdown</i>")
    return "\n".join(zeilen)


def arbeite_warteschlange(db: DB, limit: int | None = None, rueckmeldung=None) -> int:
    """Arbeitet alle 'neu'-Eintraege ab. rueckmeldung(eintrag_row, Ergebnis) z.B. fuer Telegram."""
    n = 0
    for e in db.warteschlange(limit):
        ergebnis = verarbeite(db, e["id"])
        n += 1
        if rueckmeldung:
            try:
                rueckmeldung(e, ergebnis)
            except Exception as exc:  # noqa: BLE001
                log.warning("Rueckmeldung fuer #%s fehlgeschlagen: %s", e["id"], exc)
    return n
