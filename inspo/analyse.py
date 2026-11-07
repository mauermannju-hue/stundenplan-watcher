"""Alle Claude-Aufrufe: Vorfilter (Triage), Video-Breakdown, Website-Breakdown, Ideen-Einschaetzung.
Jede Antwort ist per Structured Output an ein Pydantic-Schema gebunden."""
import base64
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import config

BEREICHE = Literal["videografie", "web", "design", "marketing", "business", "sonstiges"]
URTEIL = Literal["uebernehmen", "teilweise", "verwerfen"]


# =========================================================================== Schemas

class Triage(BaseModel):
    relevanz: int = Field(description="0-10: Wie viel koennen WIR davon lernen/uebernehmen? 0 = nichts, 10 = sofort einsetzen")
    begruendung: str = Field(description="1-2 Saetze, warum")
    bereich: BEREICHE
    kurzbeschreibung: str = Field(description="Was ist zu sehen, max. 15 Woerter")


class Technik(BaseModel):
    kategorie: Literal["hook", "struktur", "schnitt", "captions", "schrift", "farbe_look", "kamera",
                       "uebergang", "effekt", "sounddesign", "musik", "broll", "cta", "format", "sonstiges"]
    name: str = Field(description="Kurzer, wiederverwendbarer Name, z.B. 'Wort-fuer-Wort Pop-Captions' - "
                                  "gleiche Technik immer gleich benennen")
    beschreibung: str = Field(description="Was genau passiert, woran man es erkennt")
    umsetzung: str = Field(description="Konkrete Nachbau-Anleitung (CapCut zuerst, sonst Premiere/DaVinci), "
                                       "mit Werten wo moeglich (Dauer, Groesse, Position, Effektname)")
    bewertung: Literal["gut", "schlecht"]
    begruendung: str


class CaptionStil(BaseModel):
    vorhanden: bool
    schrift_vermutung: str = Field(description="Wahrscheinliche Schrift bzw. Schriftart-Klasse, z.B. 'Montserrat ExtraBold' oder 'TikTok Sans'")
    alternativen: list[str] = Field(description="Frei verfuegbare Alternativen (Google Fonts / CapCut-Schriften)")
    gewicht_und_schreibweise: str = Field(description="z.B. 'Black, GROSSBUCHSTABEN'")
    farben: str = Field(description="Textfarbe, Hervorhebungsfarbe (Hex schaetzen)")
    kontur_schatten_box: str
    position: str = Field(description="z.B. 'Mitte, ca. 60% Hoehe, ausserhalb UI-Zonen'")
    woerter_pro_einblendung: str
    animation: str = Field(description="z.B. 'Pop-in 120ms, aktives Wort gelb'")
    emojis_grafiken: str


class Szene(BaseModel):
    zeit: str = Field(description="z.B. '0:00-0:02'")
    inhalt: str
    zweck: str = Field(description="Welche Funktion hat die Szene (Hook, Beweis, Payoff, CTA ...)")


class VideoBreakdown(BaseModel):
    titel: str = Field(description="Eigener, sprechender Titel fuer die Bibliothek")
    zusammenfassung: str = Field(description="2-3 Saetze: Was ist das Video, warum funktioniert es (nicht)")
    bereich: BEREICHE
    format: str = Field(description="z.B. Talking Head, Tutorial, Vorher/Nachher, POV, Werbespot, Trend-Sound")
    hook: str = Field(description="Was passiert in den ersten 1-3 Sekunden (Bild, Text, Ton)")
    hook_bewertung: str
    struktur: list[Szene]
    tempo_und_schnitt: str = Field(description="Schnittfrequenz, Rhythmus, Jump Cuts, Speed Ramps - mit den gemessenen Zahlen")
    captions: CaptionStil
    farbe_und_look: str
    kamera_und_licht: str
    ton_musik_sfx: str
    cta: str
    staerken: list[str]
    schwaechen: list[str]
    techniken: list[Technik] = Field(description="Alle uebertragbaren Techniken - gute UND schlechte (Anti-Muster)")
    anwendung_fuer_uns: str = Field(description="Konkret: wie setzen wir das bei einem Kundenvideo um? Beispielidee mit Kunde/Branche")
    passende_branchen: list[str]
    score: int = Field(description="0-10 Gesamtqualitaet als Vorlage fuer uns")
    urteil: URTEIL = Field(description="uebernehmen = als Vorlage merken; teilweise = nur einzelne Techniken; verwerfen = eher Negativbeispiel")
    tags: list[str]


class WebTechnik(BaseModel):
    kategorie: Literal["layout", "hero", "typografie", "farbe", "bildsprache", "animation", "navigation",
                       "copy", "conversion", "vertrauen", "mobil", "sonstiges"]
    name: str
    beschreibung: str
    umsetzung: str = Field(description="Wie bauen wir das nach (Webflow/Framer/WordPress/HTML-CSS), mit Werten")
    bewertung: Literal["gut", "schlecht"]
    begruendung: str


class WebBreakdown(BaseModel):
    titel: str
    zusammenfassung: str
    bereich: BEREICHE
    zielgruppe_und_ziel: str
    aufbau: list[str] = Field(description="Abschnitte von oben nach unten")
    typografie: str = Field(description="Schriften, Groessen-Hierarchie, Paarung (nutze die gemessenen Fakten)")
    farbwelt: str = Field(description="Palette mit Hex-Werten und Rollen")
    bildsprache: str
    interaktion_animation: str
    mobil: str
    staerken: list[str]
    schwaechen: list[str]
    techniken: list[WebTechnik]
    anwendung_fuer_uns: str
    score: int
    urteil: URTEIL
    tags: list[str]


class IdeenAntwort(BaseModel):
    titel: str
    bereich: BEREICHE
    einschaetzung: str = Field(description="Ehrliche Einschaetzung in 2-4 Saetzen: lohnt sich das, Risiken")
    moeglichkeiten: list[str] = Field(description="Was man daraus machen kann (konkrete Varianten)")
    naechste_schritte: list[str]
    aufwand: Literal["klein", "mittel", "gross"]
    nutzen: Literal["klein", "mittel", "gross"]
    tags: list[str]


# =========================================================================== Claude-Aufruf

_client = None


class AnalyseFehler(RuntimeError):
    pass


def _claude():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def frage(*, modell: str, effort: str, system: list, inhalt: list, schema: type[BaseModel],
          max_tokens: int = 16000) -> BaseModel:
    """Ein strukturierter Aufruf. Bei Ablehnung (stop_reason=refusal) -> AnalyseFehler."""
    parameter = dict(
        model=modell,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": inhalt}],
        output_config={"effort": effort},
        output_format=schema,
    )
    client = _claude()
    if config.FALLBACKS and not modell.startswith("claude-haiku"):
        antwort = client.beta.messages.parse(betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                             **parameter)
    else:
        antwort = client.messages.parse(**parameter)
    if antwort.stop_reason == "refusal":
        grund = getattr(getattr(antwort, "stop_details", None), "explanation", "") or ""
        raise AnalyseFehler(f"Claude hat die Analyse abgelehnt. {grund}".strip())
    if antwort.stop_reason == "max_tokens":
        raise AnalyseFehler("Antwort abgeschnitten (max_tokens) - Bildanzahl reduzieren")
    if antwort.parsed_output is None:
        raise AnalyseFehler("Keine gueltige strukturierte Antwort erhalten")
    return antwort.parsed_output


# =========================================================================== Bausteine

def bild_block(pfad: Path) -> dict:
    daten = base64.standard_b64encode(Path(pfad).read_bytes()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": daten}}


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def _profil() -> str:
    try:
        return config.PROFIL.read_text(encoding="utf-8")
    except OSError:
        return "(kein Profil hinterlegt)"


def system_prompt(bibliothek_kurz: str = "") -> list:
    """Stabiler Teil (Rolle + Profil) wird gecacht; die wachsende Bibliothek haengt dahinter."""
    teile = [
        "Du bist das kreative Gedaechtnis unseres Teams: Du schaust dir Inspiration an, die Teammitglieder "
        "beim Scrollen finden, und zerlegst sie so, dass wir die guten Ideen spaeter gezielt wiederverwenden "
        "koennen. Du bist ein erfahrener Social-Media-Videograf, Cutter und Webdesigner.",
        "Arbeitsweise:\n"
        "- Urteile ehrlich. Viral heisst nicht automatisch gut fuer uns; auch schlechte Umsetzungen sind "
        "lehrreich (dann als Anti-Muster mit bewertung='schlecht').\n"
        "- Beschreibe nur, was auf den Bildern, im Transkript und in den Messwerten belegt ist. Wo du "
        "schaetzt (Schriftart, Farbwert, Effekt), sag das ('vermutlich').\n"
        "- Techniken muessen uebertragbar sein: ein anderer Cutter soll sie ohne das Original nachbauen "
        "koennen. Benenne Techniken, die schon in der Bibliothek stehen, exakt gleich.\n"
        "- Schreibe auf Deutsch, knapp und konkret.",
        "# Unser Profil\n" + _profil(),
    ]
    bloecke = [{"type": "text", "text": "\n\n".join(teile), "cache_control": {"type": "ephemeral"}}]
    if bibliothek_kurz:
        bloecke.append(text_block("# Bereits bekannte Techniken (gleich benennen, wenn sie wieder auftauchen)\n"
                                  + bibliothek_kurz))
    return bloecke


def _kontext(url: str | None, notiz: str | None, meta: dict) -> str:
    zeilen = []
    if url:
        zeilen.append(f"Quelle: {url}")
    if notiz:
        zeilen.append(f"Kommentar des Teammitglieds beim Teilen: {notiz}")
    for k, v in meta.items():
        if v not in (None, "", [], {}):
            text = str(v)
            zeilen.append(f"{k}: {text[:1500]}")
    return "\n".join(zeilen)


def _bilder_inhalt(bilder: list[tuple[str, Path]]) -> list:
    inhalt = []
    for beschriftung, pfad in bilder:
        inhalt.append(text_block(beschriftung))
        inhalt.append(bild_block(pfad))
    return inhalt


# =========================================================================== Aufgaben

def triage(*, url, notiz, meta, bilder, bibliothek_kurz="") -> Triage:
    inhalt = [text_block("Vorfilter: Lohnt sich eine Tiefenanalyse? Bewerte nur die Relevanz fuer uns.\n\n"
                         + _kontext(url, notiz, meta))]
    inhalt += _bilder_inhalt(bilder)
    return frage(modell=config.MODELL_TRIAGE, effort=config.EFFORT_TRIAGE, system=system_prompt(bibliothek_kurz),
                 inhalt=inhalt, schema=Triage, max_tokens=4000)


def video_breakdown(*, url, notiz, meta, bilder, transkript, bibliothek_kurz="") -> VideoBreakdown:
    anweisung = (
        "Mach einen vollstaendigen Breakdown dieses Kurzvideos. Du siehst Schluesselbilder mit Zeitstempel "
        "(das erste ist der Hook), gemessene Schnittdaten und - falls vorhanden - das Transkript. "
        "Achte besonders auf Captions/Schriften, Hook, Schnittrhythmus und was wir fuer Kundenvideos "
        "uebernehmen koennen.\n\n" + _kontext(url, notiz, meta))
    if transkript:
        anweisung += f"\n\nTranskript:\n{transkript[:6000]}"
    else:
        anweisung += "\n\n(Kein Transkript - Ton nur aus Bildern/Metadaten erschliessen und das offen sagen.)"
    inhalt = [text_block(anweisung)] + _bilder_inhalt(bilder)
    return frage(modell=config.MODELL, effort=config.EFFORT_ANALYSE, system=system_prompt(bibliothek_kurz),
                 inhalt=inhalt, schema=VideoBreakdown)


def web_breakdown(*, url, notiz, fakten, bilder, bibliothek_kurz="") -> WebBreakdown:
    anweisung = ("Analysiere diese Website als Inspiration fuer unsere eigenen Seiten und Kundenseiten. "
                 "Du siehst Screenshots (Desktop und Handy, von oben nach unten) und im Browser gemessene "
                 "Fakten.\n\n" + _kontext(url, notiz, {"gemessene_fakten": fakten}))
    inhalt = [text_block(anweisung)] + _bilder_inhalt(bilder)
    return frage(modell=config.MODELL, effort=config.EFFORT_ANALYSE, system=system_prompt(bibliothek_kurz),
                 inhalt=inhalt, schema=WebBreakdown)


def idee(*, text, absender=None, bibliothek_kurz="") -> IdeenAntwort:
    anweisung = ("Ein Teammitglied hat diese Idee/Notiz geschickt. Schaetze sie ein und zeig, was wir daraus "
                 f"machen koennen.\n\nVon: {absender or 'unbekannt'}\nIdee: {text}")
    return frage(modell=config.MODELL, effort="medium", system=system_prompt(bibliothek_kurz),
                 inhalt=[text_block(anweisung)], schema=IdeenAntwort, max_tokens=8000)


def vorschlag(*, briefing: str, bibliothek_voll: str) -> str:
    """Wendet die Bibliothek auf ein neues Projekt an: 2-3 konkrete Stil-Kombinationen."""
    anweisung = (
        "Neues Projekt. Schlage auf Basis UNSERER Stil-Bibliothek 2-3 konkrete Umsetzungsvarianten vor. "
        "Je Variante: Hook, Aufbau, Schnitt/Tempo, Caption-Preset (aus der Bibliothek, mit Rezept), Look, "
        "Sound - und nenne die Vorlagen-Nummern (#..), aus denen du schoepfst. Vermeide die Anti-Muster. "
        "Wenn die Bibliothek fuer etwas nichts hergibt, sag das und schlag selbst etwas vor. "
        "Antworte als kurze, gut scanbare Liste (Telegram, kein Markdown-Tabellen).\n\n"
        f"Briefing: {briefing}\n\n# Stil-Bibliothek\n{bibliothek_voll[:150000]}")
    client = _claude()
    parameter = dict(model=config.MODELL, max_tokens=16000, system=system_prompt(),
                     messages=[{"role": "user", "content": anweisung}], output_config={"effort": "medium"})
    if config.FALLBACKS:
        antwort = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                              **parameter)
    else:
        antwort = client.messages.create(**parameter)
    if antwort.stop_reason == "refusal":
        raise AnalyseFehler("Claude hat den Vorschlag abgelehnt.")
    return "\n".join(b.text for b in antwort.content if b.type == "text").strip()
