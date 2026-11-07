"""SQLite-Ablage: Eintraege (jeder geschickte Link/jede Idee) und die Technik-Bibliothek."""
import json
import re
import sqlite3
import threading
import unicodedata
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS eintraege (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    erstellt        TEXT NOT NULL,
    aktualisiert    TEXT NOT NULL,
    quelle          TEXT NOT NULL,          -- telegram | import | cli
    chat_id         TEXT,
    message_id      INTEGER,
    absender        TEXT,
    url             TEXT UNIQUE,            -- normalisiert; NULL bei Ideen/Dateien
    datei           TEXT,                   -- lokales Video (z.B. per Telegram geschickt)
    art             TEXT NOT NULL,          -- video | website | idee
    notiz           TEXT,                   -- was der Absender dazugeschrieben hat
    erwaehnungen    INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'neu',  -- neu | in_arbeit | fertig | verworfen | fehler
    bereich         TEXT,
    titel           TEXT,
    triage_score    INTEGER,
    triage_grund    TEXT,
    score           INTEGER,
    urteil          TEXT,                   -- uebernehmen | teilweise | verwerfen
    zusammenfassung TEXT,
    tags            TEXT,                   -- JSON-Liste
    meta_json       TEXT,                   -- Plattform-Metadaten (Views, Likes, Dauer, Schnitte ...)
    analyse_json    TEXT,                   -- vollstaendige Claude-Analyse
    markdown        TEXT,                   -- Pfad zum Breakdown
    fehler          TEXT
);
CREATE INDEX IF NOT EXISTS ix_eintraege_status ON eintraege(status);

CREATE TABLE IF NOT EXISTS techniken (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kategorie     TEXT NOT NULL,
    schluessel    TEXT NOT NULL,
    name          TEXT NOT NULL,
    beschreibung  TEXT,
    umsetzung     TEXT,
    bewertung     TEXT NOT NULL,            -- gut | schlecht
    bereich       TEXT NOT NULL,            -- videografie | web | ...
    details_json  TEXT,                     -- z.B. komplette Caption-Spezifikation
    anzahl        INTEGER NOT NULL DEFAULT 1,
    beispiele     TEXT NOT NULL DEFAULT '[]',  -- JSON-Liste von Eintrags-IDs
    erstellt      TEXT NOT NULL,
    aktualisiert  TEXT NOT NULL,
    UNIQUE(bereich, kategorie, schluessel, bewertung)
);

CREATE TABLE IF NOT EXISTS kv (
    schluessel TEXT PRIMARY KEY,
    wert       TEXT
);
"""

_lock = threading.Lock()


def jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def schluessel(text: str) -> str:
    """Vergleichsschluessel fuer Technik-Namen: klein, ohne Akzente/Sonderzeichen."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("ß", "ss")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


class DB:
    def __init__(self, pfad=None):
        config.ordner_anlegen()
        self.pfad = str(pfad or config.DB_PFAD)
        self.con = sqlite3.connect(self.pfad, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(SCHEMA)
        self.con.commit()

    # --- allgemein ------------------------------------------------------------
    def _schreibe(self, sql, params=()):
        with _lock:
            cur = self.con.execute(sql, params)
            self.con.commit()
            return cur

    def kv_get(self, key, standard=None):
        row = self.con.execute("SELECT wert FROM kv WHERE schluessel=?", (key,)).fetchone()
        return row["wert"] if row else standard

    def kv_set(self, key, wert):
        self._schreibe("INSERT INTO kv(schluessel, wert) VALUES(?, ?) "
                       "ON CONFLICT(schluessel) DO UPDATE SET wert=excluded.wert", (key, str(wert)))

    # --- Eintraege ------------------------------------------------------------
    def neuer_eintrag(self, *, art, quelle, url=None, datei=None, notiz=None, chat_id=None,
                      message_id=None, absender=None, erstellt=None):
        """Legt einen Eintrag an. Ist die URL schon bekannt, wird nur ``erwaehnungen`` erhoeht.
        Rueckgabe: (eintrag_id, ist_neu)."""
        if url:
            row = self.con.execute("SELECT id, notiz FROM eintraege WHERE url=?", (url,)).fetchone()
            if row:
                notizen = "\n".join(filter(None, [row["notiz"], notiz]))
                self._schreibe("UPDATE eintraege SET erwaehnungen=erwaehnungen+1, notiz=?, aktualisiert=? "
                               "WHERE id=?", (notizen or None, jetzt(), row["id"]))
                return row["id"], False
        ts = erstellt or jetzt()
        cur = self._schreibe(
            "INSERT INTO eintraege(erstellt, aktualisiert, quelle, chat_id, message_id, absender, url, "
            "datei, art, notiz) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ts, jetzt(), quelle, chat_id, message_id, absender, url, datei, art, notiz))
        return cur.lastrowid, True

    def eintrag(self, eintrag_id):
        return self.con.execute("SELECT * FROM eintraege WHERE id=?", (eintrag_id,)).fetchone()

    def aktualisiere(self, eintrag_id, **felder):
        for k in ("tags", "meta_json", "analyse_json"):
            if k in felder and not isinstance(felder[k], (str, type(None))):
                felder[k] = json.dumps(felder[k], ensure_ascii=False)
        felder["aktualisiert"] = jetzt()
        spalten = ", ".join(f"{k}=?" for k in felder)
        self._schreibe(f"UPDATE eintraege SET {spalten} WHERE id=?", (*felder.values(), eintrag_id))

    def warteschlange(self, limit=None):
        sql = "SELECT * FROM eintraege WHERE status='neu' ORDER BY id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.con.execute(sql).fetchall()

    def zaehle_status(self):
        return {r["status"]: r["n"] for r in
                self.con.execute("SELECT status, COUNT(*) n FROM eintraege GROUP BY status")}

    def suche(self, wort, limit=10):
        muster = f"%{wort}%"
        return self.con.execute(
            "SELECT * FROM eintraege WHERE titel LIKE ? OR zusammenfassung LIKE ? OR tags LIKE ? "
            "OR notiz LIKE ? OR url LIKE ? ORDER BY COALESCE(score, triage_score, 0) DESC, id DESC LIMIT ?",
            (muster, muster, muster, muster, muster, limit)).fetchall()

    def beste(self, limit=10, bereich=None):
        sql = "SELECT * FROM eintraege WHERE status='fertig' AND urteil != 'verwerfen'"
        params = []
        if bereich:
            sql += " AND bereich=?"
            params.append(bereich)
        sql += " ORDER BY score DESC, erwaehnungen DESC, id DESC LIMIT ?"
        params.append(limit)
        return self.con.execute(sql, params).fetchall()

    # --- Techniken ------------------------------------------------------------
    def merke_technik(self, *, eintrag_id, bereich, kategorie, name, beschreibung, umsetzung,
                      bewertung, details=None):
        """Fuegt eine Technik in die Bibliothek ein oder zaehlt eine bekannte hoch."""
        key = schluessel(name)
        row = self.con.execute(
            "SELECT id, beispiele, anzahl FROM techniken WHERE bereich=? AND kategorie=? AND schluessel=? "
            "AND bewertung=?", (bereich, kategorie, key, bewertung)).fetchone()
        details_json = json.dumps(details, ensure_ascii=False) if details else None
        if row:
            beispiele = json.loads(row["beispiele"])
            if eintrag_id in beispiele:
                return row["id"]
            beispiele.append(eintrag_id)
            self._schreibe(
                "UPDATE techniken SET anzahl=anzahl+1, beispiele=?, aktualisiert=?, "
                "umsetzung=COALESCE(NULLIF(umsetzung, ''), ?), details_json=COALESCE(details_json, ?) "
                "WHERE id=?",
                (json.dumps(beispiele), jetzt(), umsetzung, details_json, row["id"]))
            return row["id"]
        cur = self._schreibe(
            "INSERT INTO techniken(kategorie, schluessel, name, beschreibung, umsetzung, bewertung, bereich, "
            "details_json, beispiele, erstellt, aktualisiert) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (kategorie, key, name, beschreibung, umsetzung, bewertung, bereich, details_json,
             json.dumps([eintrag_id]), jetzt(), jetzt()))
        return cur.lastrowid

    def techniken(self, bereich=None, bewertung=None):
        sql = "SELECT * FROM techniken WHERE 1=1"
        params = []
        if bereich:
            sql += " AND bereich=?"
            params.append(bereich)
        if bewertung:
            sql += " AND bewertung=?"
            params.append(bewertung)
        sql += " ORDER BY kategorie, anzahl DESC, name"
        return self.con.execute(sql, params).fetchall()
