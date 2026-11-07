"""Video holen und so aufbereiten, dass Claude es 'sehen' kann:
Metadaten, Schnittzeitpunkte, Schluesselbilder und (optional) Transkript."""
import json
import re
import shutil
import subprocess
from pathlib import Path

from . import config


class VideoFehler(RuntimeError):
    pass


def _lauf(cmd, timeout=600):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise VideoFehler(f"Programm fehlt: {cmd[0]} (installieren!)") from exc


# --------------------------------------------------------------------------- Download

def lade(url: str, ziel: Path) -> tuple[Path, dict]:
    """Laedt ein Kurzvideo per yt-dlp. Rueckgabe: (Videodatei, Plattform-Infos)."""
    ziel.mkdir(parents=True, exist_ok=True)
    cmd = ["yt-dlp", "--no-playlist", "--no-progress", "--write-info-json",
           "-f", "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
           "--merge-output-format", "mp4",
           "-o", str(ziel / "video.%(ext)s"), url]
    if config.COOKIES:
        cmd[1:1] = ["--cookies", config.COOKIES]
    res = _lauf(cmd, timeout=900)
    if res.returncode != 0:
        letzte = (res.stderr or res.stdout).strip().splitlines()[-3:]
        raise VideoFehler("Download fehlgeschlagen: " + " | ".join(letzte))
    videos = [p for p in ziel.glob("video.*") if p.suffix not in (".json", ".part")]
    if not videos:
        raise VideoFehler("yt-dlp meldete Erfolg, aber keine Videodatei gefunden")
    info = {}
    info_datei = next(ziel.glob("video*.info.json"), None)
    if info_datei:
        roh = json.loads(info_datei.read_text(encoding="utf-8"))
        info = {k: roh.get(k) for k in (
            "title", "description", "uploader", "channel", "view_count", "like_count",
            "comment_count", "duration", "upload_date", "webpage_url", "extractor_key")}
        info_datei.unlink()  # roh-JSON ist riesig; das Wichtige steht in info
    return videos[0], {k: v for k, v in info.items() if v not in (None, "")}


# --------------------------------------------------------------------------- Analyse

def metadaten(pfad: Path) -> dict:
    res = _lauf(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                 str(pfad)], timeout=60)
    if res.returncode != 0:
        raise VideoFehler(f"ffprobe: {res.stderr.strip()[:200]}")
    daten = json.loads(res.stdout)
    video = next((s for s in daten["streams"] if s.get("codec_type") == "video"), None)
    if not video:
        raise VideoFehler("Datei enthaelt keine Videospur")
    zaehler, _, nenner = video.get("avg_frame_rate", "0/1").partition("/")
    fps = float(zaehler) / float(nenner or 1) if float(nenner or 1) else 0.0
    breite, hoehe = int(video.get("width", 0)), int(video.get("height", 0))
    # Handyvideos speichern Hochkant oft als gedrehtes Querformat
    rotation = 0
    for sd in video.get("side_data_list", []) or []:
        rotation = int(sd.get("rotation", 0) or 0)
    if abs(rotation) in (90, 270):
        breite, hoehe = hoehe, breite
    return {
        "dauer": round(float(daten["format"].get("duration", 0) or 0), 2),
        "breite": breite,
        "hoehe": hoehe,
        "format": "hochkant" if hoehe > breite else ("quadratisch" if hoehe == breite else "quer"),
        "fps": round(fps, 2),
        "hat_ton": any(s.get("codec_type") == "audio" for s in daten["streams"]),
    }


def schnitte(pfad: Path, schwelle: float = 0.3) -> list[float]:
    """Harte Schnitte per ffmpeg-Szenenerkennung (Zeitpunkte in Sekunden)."""
    res = _lauf(["ffmpeg", "-hide_banner", "-nostats", "-i", str(pfad), "-an",
                 "-vf", f"scale=320:-2,select='gt(scene,{schwelle})',showinfo", "-f", "null", "-"],
                timeout=600)
    zeiten = [float(t) for t in re.findall(r"pts_time:([0-9.]+)", res.stderr)]
    # sehr dicht beieinander liegende Treffer (Blitz, Glitch) zusammenfassen
    ergebnis: list[float] = []
    for t in zeiten:
        if not ergebnis or t - ergebnis[-1] > 0.25:
            ergebnis.append(round(t, 2))
    return ergebnis


def schnitt_statistik(dauer: float, cuts: list[float]) -> dict:
    einstellungen = len(cuts) + 1
    return {
        "schnitte": len(cuts),
        "einstellungen": einstellungen,
        "schnitte_pro_sekunde": round(len(cuts) / dauer, 2) if dauer else 0,
        "mittlere_einstellungslaenge": round(dauer / einstellungen, 2) if einstellungen else dauer,
        "erster_schnitt": cuts[0] if cuts else None,
    }


def zeitpunkte(dauer: float, cuts: list[float], anzahl: int) -> list[float]:
    """Waehlt die Bildzeitpunkte: immer der Hook (Anfang), dann je Einstellung die Mitte,
    bei zu vielen Einstellungen gleichmaessig ausgeduennt; bei zu wenigen aufgefuellt."""
    if dauer <= 0 or anzahl <= 0:
        return []
    grenzen = [0.0] + [c for c in cuts if 0 < c < dauer] + [dauer]
    mitten = [(a + b) / 2 for a, b in zip(grenzen, grenzen[1:]) if b - a > 0.05]
    punkte = [min(0.3, dauer / 10)]
    rest = anzahl - 1
    if len(mitten) > rest:
        schritt = len(mitten) / rest
        mitten = [mitten[int(i * schritt)] for i in range(rest)]
    punkte += mitten
    while len(punkte) < anzahl:  # auffuellen: groesste Luecke halbieren
        sortiert = sorted(punkte + [dauer])
        a, b = max(zip(sortiert, sortiert[1:]), key=lambda p: p[1] - p[0])
        if b - a < 0.2:
            break
        punkte.append((a + b) / 2)
    ende = max(0.0, dauer - 0.05)
    return sorted({round(min(p, ende), 2) for p in punkte})


def bilder(pfad: Path, zeiten: list[float], ziel: Path, lange_kante: int = 1024) -> list[tuple[float, Path]]:
    """Schluesselbilder als JPEG (lange Kante <= 1024 px - spart Tokens, Captions bleiben lesbar)."""
    ziel.mkdir(parents=True, exist_ok=True)
    ergebnis = []
    for i, t in enumerate(zeiten):
        datei = ziel / f"bild_{i:02d}_{t:06.2f}s.jpg"
        if not datei.exists():
            _lauf(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", str(pfad),
                   "-frames:v", "1",
                   "-vf", f"scale='if(gt(iw,ih),{lange_kante},-2)':'if(gt(iw,ih),-2,{lange_kante})'",
                   "-q:v", "4", str(datei)], timeout=60)
        if datei.exists() and datei.stat().st_size > 0:
            ergebnis.append((t, datei))
    return ergebnis


def transkript(pfad: Path) -> str | None:
    """Gesprochenes per faster-whisper (lokal, kostenlos) - optional. Ohne Paket: None."""
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        return None
    modell = WhisperModel(config.WHISPER_MODELL, device="auto", compute_type="int8")
    segmente, _ = modell.transcribe(str(pfad), vad_filter=True)
    zeilen = [f"[{s.start:05.1f}s] {s.text.strip()}" for s in segmente if s.text.strip()]
    return "\n".join(zeilen) or None


def aufraeumen(ordner: Path, video_behalten: bool) -> None:
    if video_behalten:
        return
    for p in ordner.glob("video.*"):
        p.unlink(missing_ok=True)


def ffmpeg_verfuegbar() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
