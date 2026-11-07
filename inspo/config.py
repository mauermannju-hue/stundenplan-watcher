"""Einstellungen - alles ueber Umgebungsvariablen oder eine ``inspo/.env``-Datei."""
import os
from pathlib import Path

PAKET = Path(__file__).resolve().parent


def _lade_env_datei(pfad: Path) -> None:
    """Minimaler .env-Leser (KEY=WERT je Zeile), damit python-dotenv keine Pflicht ist.
    Bereits gesetzte Umgebungsvariablen gewinnen."""
    if not pfad.is_file():
        return
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        key, wert = zeile.split("=", 1)
        os.environ.setdefault(key.strip(), wert.strip().strip('"').strip("'"))


_lade_env_datei(PAKET / ".env")


def _bool(name: str, standard: bool) -> bool:
    wert = os.environ.get(name)
    if wert is None:
        return standard
    return wert.strip().lower() in ("1", "true", "ja", "yes", "on")


# --- Ablage ------------------------------------------------------------------
DATEN = Path(os.environ.get("INSPO_DATEN", PAKET / "daten")).resolve()
DB_PFAD = DATEN / "inspo.db"
MEDIEN = DATEN / "medien"              # heruntergeladene Videos + Einzelbilder
BIBLIOTHEK = DATEN / "bibliothek"      # Markdown-Breakdowns + STIL-BIBLIOTHEK.md
PROFIL = Path(os.environ.get("INSPO_PROFIL", PAKET / "profil.md"))

# --- Claude ------------------------------------------------------------------
MODELL = os.environ.get("INSPO_MODELL", "claude-opus-5-5")
# Vorfilter laeuft fuer JEDEN Link - wer Kosten sparen will, setzt hier z.B. claude-haiku-5-5.
MODELL_TRIAGE = os.environ.get("INSPO_MODELL_TRIAGE", MODELL)
EFFORT_ANALYSE = os.environ.get("INSPO_EFFORT", "high")
EFFORT_TRIAGE = os.environ.get("INSPO_EFFORT_TRIAGE", "low")
# Server-seitiger Fallback bei Ablehnungen; auf 0 setzen, falls ein Proxy/Plattform ihn nicht kennt.
FALLBACKS = _bool("INSPO_FALLBACKS", True)

# --- Pipeline ----------------------------------------------------------------
SCHWELLE = int(os.environ.get("INSPO_SCHWELLE", "6"))         # Triage-Score 0-10 fuer Tiefenanalyse
BILDER_TRIAGE = int(os.environ.get("INSPO_BILDER_TRIAGE", "4"))
BILDER_ANALYSE = int(os.environ.get("INSPO_BILDER_ANALYSE", "16"))
MAX_VIDEO_SEKUNDEN = int(os.environ.get("INSPO_MAX_SEKUNDEN", "240"))  # Longform wird nur getriagt
VIDEOS_BEHALTEN = _bool("INSPO_VIDEOS_BEHALTEN", True)       # nur fuer "uebernehmen"/"teilweise"
COOKIES = os.environ.get("INSPO_COOKIES", "")                # cookies.txt fuer Instagram/TikTok
WHISPER_MODELL = os.environ.get("INSPO_WHISPER", "small")    # nur wenn faster-whisper installiert ist
CHROMIUM = os.environ.get("INSPO_CHROMIUM", "")              # optionaler Pfad zu Chromium

# --- Telegram ----------------------------------------------------------------
TG_TOKEN = os.environ.get("INSPO_TG_TOKEN", "")
# Kommagetrennte Chat-IDs, die der Bot bedienen darf (Gruppe + Privatchats). Leer = alle (nur zum Einrichten!).
TG_CHATS = {c.strip() for c in os.environ.get("INSPO_TG_CHATS", "").split(",") if c.strip()}
# In Gruppen nur per Reaktion (👀/🔥/👌/🥱) antworten statt mit Text - haelt die Inspo-Gruppe sauber.
GRUPPE_LEISE = _bool("INSPO_GRUPPE_LEISE", True)


def ordner_anlegen() -> None:
    for p in (DATEN, MEDIEN, BIBLIOTHEK, BIBLIOTHEK / "eintraege"):
        p.mkdir(parents=True, exist_ok=True)
