"""Links aus Nachrichten ziehen, einordnen und Chat-Exporte (WhatsApp/Telegram) einlesen."""
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+", re.IGNORECASE)

# Plattformen, deren Links wir als Kurzvideo behandeln
VIDEO_HOSTS = (
    "instagram.com", "tiktok.com", "youtube.com", "youtu.be", "facebook.com", "fb.watch",
    "vimeo.com", "x.com", "twitter.com", "pinterest.", "pin.it", "threads.net", "snapchat.com",
)
# Auf diesen Hosts ist nicht jeder Pfad ein Video
VIDEO_PFADE = {
    "instagram.com": ("/reel", "/reels", "/p/", "/tv/", "/share/"),
    "youtube.com": ("/shorts/", "/watch", "/live/"),
    "facebook.com": ("/reel", "/watch", "/videos/", "/share/"),
    "x.com": ("/status/",),
    "twitter.com": ("/status/",),
}
TRACKING = re.compile(r"^(utm_.*|igsh|igshid|si|feature|fbclid|gclid|mibextid|_r|_t|is_from_webapp|"
                      r"sender_device|share_.*|ref|ref_src|s)$", re.IGNORECASE)


def finde_urls(text: str) -> list[str]:
    if not text:
        return []
    gesehen, urls = set(), []
    for roh in URL_RE.findall(text):
        url = normalisiere(roh.rstrip(".,;:!?"))
        if url not in gesehen:
            gesehen.add(url)
            urls.append(url)
    return urls


def text_ohne_urls(text: str) -> str:
    return re.sub(r"\s+", " ", URL_RE.sub(" ", text or "")).strip()


def normalisiere(url: str) -> str:
    """Tracking-Parameter entfernen, damit derselbe Reel nicht doppelt analysiert wird."""
    teile = urlsplit(url)
    host = teile.netloc.lower()
    if host.startswith("www.") or host.startswith("m."):
        host = host.split(".", 1)[1]
    query = [(k, v) for k, v in parse_qsl(teile.query, keep_blank_values=True) if not TRACKING.match(k)]
    pfad = teile.path
    if host.endswith("instagram.com"):
        pfad = pfad.rstrip("/") + "/"
        query = []
    return urlunsplit((teile.scheme.lower(), host, pfad, urlencode(query), ""))


def art_von(url: str) -> str:
    """'video' fuer Social-/Videoplattformen, sonst 'website'."""
    host = urlsplit(url).netloc.lower()
    pfad = urlsplit(url).path.lower()
    for vh in VIDEO_HOSTS:
        if vh in host:
            pfade = next((p for h, p in VIDEO_PFADE.items() if host.endswith(h)), None)
            if pfade is None or any(pfad.startswith(p) for p in pfade):
                return "video"
            if host.endswith("tiktok.com") or host.endswith("youtu.be"):
                return "video"
    if re.search(r"\.(mp4|mov|webm|m4v)$", pfad):
        return "video"
    return "website"


# --------------------------------------------------------------------------- Chat-Exporte

class Nachricht(dict):
    """{'zeit': iso-str|None, 'absender': str, 'text': str}"""


_WA_ZEILE = re.compile(
    r"^\[?(?P<datum>\d{1,2}[./]\d{1,2}[./]\d{2,4}),?\s+(?P<zeit>\d{1,2}:\d{2}(?::\d{2})?)\]?"
    r"\s*(?:-\s*)?(?P<rest>.*)$")


def lese_whatsapp(pfad: Path) -> list[Nachricht]:
    """WhatsApp 'Chat exportieren' (ohne Medien) -> Nachrichten. Mehrzeilige Nachrichten werden
    an die vorherige angehaengt. Deckt deutsches und englisches Format ab."""
    nachrichten: list[Nachricht] = []
    for zeile in Path(pfad).read_text(encoding="utf-8", errors="replace").splitlines():
        zeile = zeile.replace("‎", "").replace(" ", " ")
        m = _WA_ZEILE.match(zeile)
        if m and ":" in m.group("rest"):
            absender, text = m.group("rest").split(":", 1)
            nachrichten.append(Nachricht(zeit=_wa_zeit(m.group("datum"), m.group("zeit")),
                                         absender=absender.strip(), text=text.strip()))
        elif m:
            continue  # Systemmeldung ("X hat die Gruppe erstellt")
        elif nachrichten:
            nachrichten[-1]["text"] += "\n" + zeile
    return nachrichten


def _wa_zeit(datum: str, zeit: str):
    datum = datum.replace("/", ".")
    for fmt in ("%d.%m.%y %H:%M", "%d.%m.%Y %H:%M", "%d.%m.%y %H:%M:%S", "%d.%m.%Y %H:%M:%S"):
        try:
            return datetime.strptime(f"{datum} {zeit}", fmt).isoformat()
        except ValueError:
            pass
    return None


def lese_telegram(pfad: Path) -> list[Nachricht]:
    """Telegram Desktop -> 'Chatverlauf exportieren' -> Format JSON (result.json)."""
    daten = json.loads(Path(pfad).read_text(encoding="utf-8"))
    nachrichten: list[Nachricht] = []
    for m in daten.get("messages", []):
        if m.get("type") != "message":
            continue
        text = m.get("text", "")
        if isinstance(text, list):  # Telegram zerlegt formatierten Text in Teile
            teile = []
            for t in text:
                if isinstance(t, str):
                    teile.append(t)
                else:
                    teile.append(t.get("href") or t.get("text", ""))
            text = "".join(teile)
        nachrichten.append(Nachricht(zeit=m.get("date"), absender=m.get("from") or "", text=text))
    return nachrichten


def lese_export(pfad: Path) -> list[Nachricht]:
    pfad = Path(pfad)
    if pfad.suffix.lower() == ".json":
        return lese_telegram(pfad)
    return lese_whatsapp(pfad)
