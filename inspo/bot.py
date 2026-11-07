"""Telegram-Bot: sitzt als Mitglied in der Inspo-Gruppe (und nimmt Privatnachrichten an),
sammelt jeden Link, analysiert im Hintergrund und antwortet per Reaktion oder Nachricht."""
import html
import logging
import threading
import time
from pathlib import Path

import requests

from . import analyse, bibliothek, config, ingest
from .db import DB
from .verarbeitung import arbeite_warteschlange, verarbeite

log = logging.getLogger("inspo.bot")

HILFE = """<b>Inspo-Inbox</b> – schick mir alles, was dich inspiriert.

• <b>Link zu Reel/TikTok/Short</b> → ich lade es, filtere nach Relevanz und mache bei guten Videos einen kompletten Breakdown (Hook, Schnitt, Captions/Schrift, Look, Sound) und merke mir die Techniken.
• <b>Link zu einer Website</b> → Screenshots + Analyse (Layout, Schriften, Farben, Conversion).
• <b>Video-Datei</b> (bis 20 MB) → wie ein Link.
• <b>Text dazu</b> → wird als Kommentar mitgespeichert („Hook ist krass“, „für Kunde X“).
• <b>Idee ohne Link</b> → im Privatchat einfach schreiben, in der Gruppe mit /idee.

<b>Befehle</b>
/idee &lt;Text&gt; – Idee einschätzen lassen
/notiz &lt;Text&gt; – nur merken, ohne Analyse
/web &lt;URL&gt; – Link als Website behandeln
/vorschlag &lt;Briefing&gt; – Bibliothek auf ein neues Projekt anwenden
/analyse &lt;Nr&gt; – vollen Breakdown erzwingen (auch wenn vorgefiltert)
/details &lt;Nr&gt; – Breakdown als Datei
/suche &lt;Wort&gt; – in allem Gesammelten suchen
/top – die besten Vorlagen
/bibliothek – die Stil-Bibliothek als Datei
/status – Warteschlange
/id – Chat-ID (für die Einrichtung)

Reaktionen: 👀 angekommen · 🔥 Vorlage gemerkt · 👌 einzelne Techniken gemerkt · 🥱 nicht relevant · 🤷 Fehler"""

REAKTION = {"uebernehmen": "🔥", "teilweise": "👌", "verwerfen": "🥱", "fehler": "🤷"}


class Telegram:
    def __init__(self, token: str):
        self.basis = f"https://api.telegram.org/bot{token}"
        self.datei_basis = f"https://api.telegram.org/file/bot{token}"
        self.http = requests.Session()

    def _api(self, methode, timeout=30, **params):
        r = self.http.post(f"{self.basis}/{methode}", timeout=timeout, **params)
        daten = r.json()
        if not daten.get("ok"):
            raise RuntimeError(f"Telegram {methode}: {daten.get('description')}")
        return daten["result"]

    def updates(self, offset, timeout=50):
        return self._api("getUpdates", timeout=timeout + 10,
                         json={"offset": offset, "timeout": timeout,
                               "allowed_updates": ["message", "channel_post"]})

    def ich(self):
        return self._api("getMe")

    def sende(self, chat_id, text, antwort_auf=None):
        for i, stueck in enumerate(_teile(text)):
            daten = {"chat_id": chat_id, "text": stueck, "parse_mode": "HTML",
                     "link_preview_options": {"is_disabled": True}}
            if antwort_auf and i == 0:
                daten["reply_parameters"] = {"message_id": antwort_auf, "allow_sending_without_reply": True}
            self._api("sendMessage", json=daten)

    def reagiere(self, chat_id, message_id, emoji):
        try:
            self._api("setMessageReaction", json={"chat_id": chat_id, "message_id": message_id,
                                                  "reaction": [{"type": "emoji", "emoji": emoji}]})
        except Exception as exc:  # noqa: BLE001 - Reaktionen sind Kuer
            log.debug("Reaktion fehlgeschlagen: %s", exc)

    def sende_datei(self, chat_id, pfad: Path, caption=""):
        with open(pfad, "rb") as fh:
            self._api("sendDocument", timeout=120, data={"chat_id": chat_id, "caption": caption[:1000],
                                                         "parse_mode": "HTML"},
                      files={"document": (Path(pfad).name, fh)})

    def lade_datei(self, file_id, ziel: Path) -> Path:
        info = self._api("getFile", json={"file_id": file_id})
        ziel.parent.mkdir(parents=True, exist_ok=True)
        with self.http.get(f"{self.datei_basis}/{info['file_path']}", stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(ziel, "wb") as fh:
                for block in r.iter_content(1 << 16):
                    fh.write(block)
        return ziel


def _teile(text, limit=3900):
    if len(text) <= limit:
        return [text]
    teile, aktuell = [], ""
    for zeile in text.split("\n"):
        if len(aktuell) + len(zeile) + 1 > limit and aktuell:
            teile.append(aktuell)
            aktuell = ""
        aktuell += zeile + "\n"
    return teile + [aktuell] if aktuell else teile


class InspoBot:
    def __init__(self, db: DB, tg: Telegram):
        self.db, self.tg = db, tg
        self.benutzername = ""
        self.signal = threading.Event()
        self.erzwingen: set[int] = set()
        self.privat: dict[int, bool] = {}   # eintrag_id -> kam aus Privatchat?

    # ------------------------------------------------------------------ Schleifen
    def starte(self):
        self.benutzername = self.tg.ich().get("username", "")
        log.info("Bot @%s laeuft. Erlaubte Chats: %s", self.benutzername, config.TG_CHATS or "ALLE (Einrichtung)")
        threading.Thread(target=self._arbeiter, daemon=True, name="inspo-arbeiter").start()
        offset = int(self.db.kv_get("tg_offset", 0))
        while True:
            try:
                for upd in self.tg.updates(offset):
                    offset = upd["update_id"] + 1
                    self.db.kv_set("tg_offset", offset)
                    try:
                        self.verarbeite_update(upd)
                    except Exception:  # noqa: BLE001
                        log.exception("Update %s fehlgeschlagen", upd.get("update_id"))
            except requests.RequestException as exc:
                log.warning("Telegram nicht erreichbar (%s) - neuer Versuch in 10 s", exc)
                time.sleep(10)

    def _arbeiter(self):
        while True:
            self.signal.wait(timeout=300)
            self.signal.clear()
            for eid in sorted(self.erzwingen):
                self.erzwingen.discard(eid)
                self._melde(self.db.eintrag(eid), verarbeite(self.db, eid, erzwingen=True))
            arbeite_warteschlange(self.db, rueckmeldung=self._melde)

    def _melde(self, e, ergebnis):
        if not e["chat_id"]:
            return
        status = ergebnis.get("urteil") or ergebnis.get("status")
        if e["message_id"]:
            self.tg.reagiere(e["chat_id"], e["message_id"], REAKTION.get(status, "👌"))
        privat = self.privat.pop(e["id"], not str(e["chat_id"]).startswith("-"))
        if privat or not config.GRUPPE_LEISE:
            self.tg.sende(e["chat_id"], ergebnis["text"], antwort_auf=e["message_id"])

    # ------------------------------------------------------------------ Eingang
    def verarbeite_update(self, upd):
        m = upd.get("message") or upd.get("channel_post")
        if not m:
            return
        chat_id = str(m["chat"]["id"])
        privat = m["chat"]["type"] == "private"
        text = m.get("text") or m.get("caption") or ""
        absender = (m.get("from") or {}).get("first_name") or m["chat"].get("title", "")

        if text.split(" ")[0].split("@")[0] == "/id":  # vor der Freigabepruefung: noetig zum Einrichten
            self.tg.sende(chat_id, f"Chat-ID: <code>{chat_id}</code>")
            return
        if config.TG_CHATS and chat_id not in config.TG_CHATS:
            log.info("Nachricht aus nicht freigegebenem Chat %s ignoriert", chat_id)
            return

        if text.startswith("/"):
            befehl, _, rest = text.partition(" ")
            befehl = befehl[1:].split("@")[0].lower()
            return self._befehl(befehl, rest.strip(), m, chat_id, privat, absender)

        # versteckte Links (formatierter Text) mitnehmen
        urls = ingest.finde_urls(text)
        for ent in (m.get("entities") or []) + (m.get("caption_entities") or []):
            if ent.get("type") == "text_link":
                urls += [u for u in ingest.finde_urls(ent["url"]) if u not in urls]
        notiz = ingest.text_ohne_urls(text) or None
        neu = []

        datei = m.get("video") or m.get("video_note") or m.get("animation")
        if not datei and (m.get("document") or {}).get("mime_type", "").startswith("video/"):
            datei = m["document"]
        if datei:
            if datei.get("file_size", 0) > 20 * 1024 * 1024:
                self.tg.sende(chat_id, "Die Datei ist größer als 20 MB – Bots dürfen sie nicht laden. "
                                       "Schick mir lieber den Link.", antwort_auf=m["message_id"])
            else:
                ziel = config.MEDIEN / "telegram" / f"{datei['file_unique_id']}.mp4"
                self.tg.lade_datei(datei["file_id"], ziel)
                neu.append(self.db.neuer_eintrag(art="video", quelle="telegram", datei=str(ziel), notiz=notiz,
                                                 chat_id=chat_id, message_id=m["message_id"], absender=absender))

        for url in urls:
            neu.append(self.db.neuer_eintrag(art=ingest.art_von(url), quelle="telegram", url=url, notiz=notiz,
                                             chat_id=chat_id, message_id=m["message_id"], absender=absender))

        if not neu:
            erwaehnt = self.benutzername and f"@{self.benutzername}".lower() in text.lower()
            antwort_an_bot = ((m.get("reply_to_message") or {}).get("from") or {}).get("username") == self.benutzername
            if privat or erwaehnt or antwort_an_bot:
                neu.append(self.db.neuer_eintrag(art="idee", quelle="telegram", notiz=text, chat_id=chat_id,
                                                 message_id=m["message_id"], absender=absender))
            else:
                return  # normales Gespraech in der Gruppe

        self._quittiere(m, chat_id, privat, neu)

    def _quittiere(self, m, chat_id, privat, neu):
        self.tg.reagiere(chat_id, m["message_id"], "👀")
        frisch = [eid for eid, ist_neu in neu if ist_neu]
        for eid in frisch:
            self.privat[eid] = privat
        if privat:
            teile = []
            for eid, ist_neu in neu:
                e = self.db.eintrag(eid)
                if ist_neu:
                    teile.append(f"#{eid} ist in der Warteschlange.")
                else:
                    teile.append(f"#{eid} kenne ich schon ({e['erwaehnungen']}x geteilt, Status: {e['status']}).")
            self.tg.sende(chat_id, " ".join(teile), antwort_auf=m["message_id"])
        if frisch:
            self.signal.set()

    # ------------------------------------------------------------------ Befehle
    def _befehl(self, befehl, rest, m, chat_id, privat, absender):
        antwort = lambda t: self.tg.sende(chat_id, t, antwort_auf=m["message_id"])  # noqa: E731
        if befehl in ("start", "hilfe", "help"):
            antwort(HILFE)
        elif befehl == "idee" and rest:
            eid, _ = self.db.neuer_eintrag(art="idee", quelle="telegram", notiz=rest, chat_id=chat_id,
                                           message_id=m["message_id"], absender=absender)
            self.privat[eid] = True  # Ideen bekommen immer eine Antwort
            self.tg.reagiere(chat_id, m["message_id"], "👀")
            self.signal.set()
        elif befehl == "notiz" and rest:
            eid, _ = self.db.neuer_eintrag(art="idee", quelle="telegram", notiz=rest, chat_id=chat_id,
                                           message_id=m["message_id"], absender=absender)
            self.db.aktualisiere(eid, status="fertig", titel=rest[:80], urteil="teilweise")
            self.tg.reagiere(chat_id, m["message_id"], "✍")
        elif befehl == "web" and rest:
            for url in ingest.finde_urls(rest):
                eid, ist_neu = self.db.neuer_eintrag(art="website", quelle="telegram", url=url, chat_id=chat_id,
                                                     message_id=m["message_id"], absender=absender)
                if not ist_neu:
                    self.db.aktualisiere(eid, art="website", status="neu")
                self.privat[eid] = privat
            self.tg.reagiere(chat_id, m["message_id"], "👀")
            self.signal.set()
        elif befehl == "analyse" and rest.lstrip("#").isdigit():
            eid = int(rest.lstrip("#"))
            if not self.db.eintrag(eid):
                return antwort(f"#{eid} gibt es nicht.")
            self.privat[eid] = True
            self.erzwingen.add(eid)
            self.signal.set()
            antwort(f"Okay, #{eid} bekommt den vollen Breakdown.")
        elif befehl == "vorschlag" and rest:
            self.tg.reagiere(chat_id, m["message_id"], "✍")
            datei = bibliothek.schreibe_bibliothek(self.db)

            def los():
                try:
                    text = analyse.vorschlag(briefing=rest, bibliothek_voll=datei.read_text(encoding="utf-8"))
                    antwort(html.escape(text))
                except Exception as exc:  # noqa: BLE001
                    antwort(f"⚠️ Vorschlag ging schief: {html.escape(str(exc)[:300])}")
            threading.Thread(target=los, daemon=True).start()
        elif befehl == "details" and rest.lstrip("#").isdigit():
            e = self.db.eintrag(int(rest.lstrip("#")))
            if not e or not e["markdown"] or not Path(e["markdown"]).exists():
                return antwort("Dazu gibt es noch keinen Breakdown.")
            self.tg.sende_datei(chat_id, Path(e["markdown"]), caption=f"#{e['id']} {html.escape(e['titel'] or '')}")
        elif befehl == "suche" and rest:
            treffer = self.db.suche(rest)
            antwort(_liste(treffer) or "Nichts gefunden.")
        elif befehl == "top":
            antwort(_liste(self.db.beste(limit=10)) or "Noch keine Vorlagen gesammelt.")
        elif befehl == "bibliothek":
            datei = bibliothek.schreibe_bibliothek(self.db)
            gut = self.db.techniken(bewertung="gut")
            top = sorted(gut, key=lambda t: -t["anzahl"])[:8]
            text = "<b>Meistgemerkte Techniken</b>\n" + "\n".join(
                f"• {html.escape(t['name'])} ({t['anzahl']}x, {t['kategorie']})" for t in top)
            self.tg.sende_datei(chat_id, datei, caption=f"{len(gut)} Techniken in der Bibliothek")
            if top:
                antwort(text)
        elif befehl == "status":
            z = self.db.zaehle_status()
            antwort("Status: " + ", ".join(f"{k}: {v}" for k, v in sorted(z.items())) if z else "Noch leer.")
        else:
            antwort("Den Befehl kenne ich nicht (oder es fehlt etwas dahinter). /hilfe")


def _liste(zeilen) -> str:
    return "\n".join(
        f"{bibliothek.URTEIL_EMOJI.get(e['urteil'], '•')} <b>#{e['id']}</b> {html.escape(e['titel'] or e['url'] or '')}"
        f" – {e['score'] if e['score'] is not None else '?'}/10" for e in zeilen)


def main():
    if not config.TG_TOKEN:
        raise SystemExit("INSPO_TG_TOKEN fehlt (Bot bei @BotFather anlegen).")
    InspoBot(DB(), Telegram(config.TG_TOKEN)).starte()
