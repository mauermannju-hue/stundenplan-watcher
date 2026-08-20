#!/usr/bin/env python3
"""Rendert einen Tagesplan als PNG-Karte fuer Telegram.

Braucht Pillow. Ist Pillow oder eine brauchbare Schrift nicht da, meldet
verfuegbar() False und der Aufrufer schickt die Textfassung.
"""

import os

BREITE = 720
RAND = 32
KOPF_HOEHE = 132
ZEILE_HOEHE = 68
FUSS_HOEHE = 58
SKALA = 2                      # doppelt rendern, damit es auf Retina scharf ist

BG = (18, 20, 24)
KARTE = (28, 31, 38)
KARTE_ALT = (33, 37, 45)
TEXT = (232, 234, 237)
MUTED = (150, 157, 167)
TRENNER = (48, 53, 62)

FARBE = {
    "normal": (70, 78, 90),
    "geaendert": (224, 160, 32),
    "ausfall": (224, 82, 60),
}

SCHRIFTEN = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
SCHRIFTEN_FETT = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _erste(pfade):
    for p in pfade:
        if os.path.exists(p):
            return p
    return None


def verfuegbar():
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return bool(_erste(SCHRIFTEN) and _erste(SCHRIFTEN_FETT))


def rendere(klasse, datum, bloecke, ziel):
    """bloecke: Liste von dicts aus watcher.zusammenfassen(). Gibt den Pfad zurueck."""
    from PIL import Image, ImageDraw, ImageFont

    regular, fett = _erste(SCHRIFTEN), _erste(SCHRIFTEN_FETT)
    f_titel = ImageFont.truetype(fett, 34 * SKALA)
    f_datum = ImageFont.truetype(regular, 22 * SKALA)
    f_zeit = ImageFont.truetype(regular, 21 * SKALA)
    f_fach = ImageFont.truetype(fett, 25 * SKALA)
    f_detail = ImageFont.truetype(regular, 21 * SKALA)
    f_fuss = ImageFont.truetype(regular, 18 * SKALA)

    zeilen = bloecke or [None]
    hoehe = KOPF_HOEHE + len(zeilen) * ZEILE_HOEHE + FUSS_HOEHE
    img = Image.new("RGB", (BREITE * SKALA, hoehe * SKALA), BG)
    d = ImageDraw.Draw(img)

    def kasten(x, y, w, h, farbe, radius=0):
        box = [x * SKALA, y * SKALA, (x + w) * SKALA, (y + h) * SKALA]
        if radius:
            d.rounded_rectangle(box, radius=radius * SKALA, fill=farbe)
        else:
            d.rectangle(box, fill=farbe)

    def schrift(x, y, text, font, farbe):
        d.text((x * SKALA, y * SKALA), text, font=font, fill=farbe)

    # Kopf
    schrift(RAND, 34, klasse, f_titel, TEXT)
    schrift(RAND, 80, datum, f_datum, MUTED)
    d.line([(RAND * SKALA, (KOPF_HOEHE - 14) * SKALA),
            ((BREITE - RAND) * SKALA, (KOPF_HOEHE - 14) * SKALA)], fill=TRENNER, width=SKALA)

    y = KOPF_HOEHE
    if not bloecke:
        schrift(RAND, y + 20, "Kein Unterricht im Plan", f_detail, MUTED)
        y += ZEILE_HOEHE
    for i, b in enumerate(bloecke):
        kasten(RAND, y, BREITE - 2 * RAND, ZEILE_HOEHE - 8,
               KARTE if i % 2 == 0 else KARTE_ALT, radius=10)
        kasten(RAND, y, 6, ZEILE_HOEHE - 8, FARBE.get(b["art"], FARBE["normal"]), radius=3)

        schrift(RAND + 24, y + 8, b["zeit"], f_zeit, MUTED)
        schrift(RAND + 24, y + 32, b["stunden"], f_fuss, MUTED)

        x_fach = RAND + 168
        if b["art"] == "ausfall":
            schrift(x_fach, y + 9, b["fach"] or "entfällt", f_fach, FARBE["ausfall"])
            # Der Info-Text wiederholt fast immer das Fach ("LF5 Frau Pohl faellt aus") -
            # das steht schon fett daneben, also vorn abschneiden.
            info = b["info"]
            if info and b["fach"] and info.upper().startswith(b["fach"].upper()):
                info = info[len(b["fach"]):].lstrip(" -–:")
            schrift(x_fach, y + 39, info or "fällt aus", f_detail, MUTED)
        else:
            schrift(x_fach, y + 9, b["fach"], f_fach, TEXT)
            unten = " · ".join(x for x in (b["lehrer"], b["raum"]) if x)
            if b["info"]:
                unten = f"{unten} · {b['info']}" if unten else b["info"]
            schrift(x_fach, y + 39, unten, f_detail, MUTED)
        y += ZEILE_HOEHE

    schrift(RAND, y + 12, "rot = Ausfall   ·   gelb = geändert", f_fuss, MUTED)

    img.save(ziel, "PNG", optimize=True)
    return ziel
