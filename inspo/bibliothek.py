"""Aus Analysen wird Wissen: Techniken merken, Breakdowns als Markdown ablegen,
die Stil-Bibliothek schreiben und als Claude-Skill exportieren."""
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from . import config
from .db import DB

KATEGORIE_TITEL = {
    "hook": "Hooks", "struktur": "Aufbau & Dramaturgie", "schnitt": "Schnitt & Tempo",
    "captions": "Captions", "schrift": "Schriften", "farbe_look": "Farbe & Look",
    "kamera": "Kamera & Licht", "uebergang": "Uebergaenge", "effekt": "Effekte",
    "sounddesign": "Sounddesign", "musik": "Musik", "broll": "B-Roll", "cta": "Calls to Action",
    "format": "Formate", "layout": "Layout", "hero": "Hero-Bereiche", "typografie": "Typografie",
    "farbe": "Farbwelt", "bildsprache": "Bildsprache", "animation": "Animation",
    "navigation": "Navigation", "copy": "Texte", "conversion": "Conversion", "vertrauen": "Vertrauen",
    "mobil": "Mobil", "sonstiges": "Sonstiges",
}
URTEIL_EMOJI = {"uebernehmen": "🔥", "teilweise": "👌", "verwerfen": "🥱"}


def slug(text: str, laenge: int = 50) -> str:
    text = (text or "eintrag").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:laenge] or "eintrag"


# --------------------------------------------------------------------------- Techniken

def merke(db: DB, eintrag_id: int, analyse: dict, bereich_bibliothek: str) -> int:
    """Uebernimmt alle Techniken einer Analyse in die Bibliothek. Caption-Stile werden als eigene
    'captions'-Technik mit kompletter Spezifikation gespeichert (= Caption-Preset)."""
    n = 0
    for t in analyse.get("techniken", []):
        db.merke_technik(eintrag_id=eintrag_id, bereich=bereich_bibliothek, kategorie=t["kategorie"],
                         name=t["name"], beschreibung=t["beschreibung"], umsetzung=t["umsetzung"],
                         bewertung=t["bewertung"])
        n += 1
    cap = analyse.get("captions")
    if cap and cap.get("vorhanden") and analyse.get("urteil") != "verwerfen":
        name = f"Caption-Preset: {cap['schrift_vermutung']} – {cap['gewicht_und_schreibweise']}"
        db.merke_technik(eintrag_id=eintrag_id, bereich=bereich_bibliothek, kategorie="captions", name=name,
                         beschreibung=cap["animation"], umsetzung=_caption_rezept(cap), bewertung="gut",
                         details=cap)
        n += 1
    return n


def _caption_rezept(cap: dict) -> str:
    return (f"Schrift {cap['schrift_vermutung']} (Alternativen: {', '.join(cap['alternativen']) or '–'}), "
            f"{cap['gewicht_und_schreibweise']}; Farben: {cap['farben']}; {cap['kontur_schatten_box']}; "
            f"Position: {cap['position']}; {cap['woerter_pro_einblendung']} Woerter je Einblendung; "
            f"Animation: {cap['animation']}")


def kurzfassung(db: DB, max_zeilen: int = 120) -> str:
    """Kompakte Liste bekannter Techniken fuer den System-Prompt (damit Namen konsistent bleiben)."""
    zeilen = []
    for t in db.techniken():
        zeichen = "+" if t["bewertung"] == "gut" else "−"
        zeilen.append(f"{zeichen} [{t['bereich']}/{t['kategorie']}] {t['name']} ({t['anzahl']}x)")
    return "\n".join(zeilen[:max_zeilen])


# --------------------------------------------------------------------------- Markdown je Eintrag

def schreibe_eintrag(db: DB, eintrag_id: int, bilder: list[tuple[str, Path]] | None = None) -> Path:
    e = db.eintrag(eintrag_id)
    a = json.loads(e["analyse_json"] or "{}")
    meta = json.loads(e["meta_json"] or "{}")
    ordner = config.BIBLIOTHEK / "eintraege" / f"{eintrag_id:04d}-{slug(e['titel'])}"
    ordner.mkdir(parents=True, exist_ok=True)

    bild_md = []
    for beschriftung, pfad in (bilder or []):
        ziel = ordner / Path(pfad).name
        if Path(pfad).resolve() != ziel.resolve():
            shutil.copy2(pfad, ziel)
        bild_md.append(f"![{beschriftung}]({ziel.name})")

    z = [f"# {URTEIL_EMOJI.get(e['urteil'], '')} {e['titel']}".strip(), ""]
    z.append(f"- **Quelle:** {e['url'] or e['datei'] or '–'}")
    z.append(f"- **Geschickt von:** {e['absender'] or '–'} am {e['erstellt'][:10]}"
             + (f" ({e['erwaehnungen']}x geteilt)" if e["erwaehnungen"] > 1 else ""))
    if e["notiz"]:
        z.append(f"- **Kommentar:** {e['notiz']}")
    z.append(f"- **Bereich:** {e['bereich']} · **Score:** {e['score']}/10 · **Urteil:** {e['urteil']}")
    if meta:
        fakten = {k: meta[k] for k in ("dauer", "format", "schnitte", "mittlere_einstellungslaenge",
                                       "view_count", "like_count", "uploader") if k in meta}
        if fakten:
            z.append("- **Messwerte:** " + " · ".join(f"{k}: {v}" for k, v in fakten.items()))
    if a.get("tags"):
        z.append("- **Tags:** " + ", ".join(f"#{t}" for t in a["tags"]))
    z += ["", a.get("zusammenfassung", ""), ""]

    if e["art"] == "video":
        z += _abschnitt("Hook", f"{a.get('hook', '')}\n\n_{a.get('hook_bewertung', '')}_")
        if a.get("struktur"):
            z += ["## Aufbau", "", "| Zeit | Inhalt | Zweck |", "|---|---|---|"]
            z += [f"| {s['zeit']} | {s['inhalt']} | {s['zweck']} |" for s in a["struktur"]]
            z.append("")
        z += _abschnitt("Tempo & Schnitt", a.get("tempo_und_schnitt"))
        cap = a.get("captions") or {}
        if cap.get("vorhanden"):
            z += ["## Captions", ""] + [f"- **{k.replace('_', ' ')}:** {', '.join(v) if isinstance(v, list) else v}"
                                        for k, v in cap.items() if k != "vorhanden"] + [""]
        z += _abschnitt("Farbe & Look", a.get("farbe_und_look"))
        z += _abschnitt("Kamera & Licht", a.get("kamera_und_licht"))
        z += _abschnitt("Ton, Musik, SFX", a.get("ton_musik_sfx"))
        z += _abschnitt("Call to Action", a.get("cta"))
    elif e["art"] == "website":
        z += _abschnitt("Ziel & Zielgruppe", a.get("zielgruppe_und_ziel"))
        if a.get("aufbau"):
            z += ["## Aufbau", ""] + [f"{i}. {s}" for i, s in enumerate(a["aufbau"], 1)] + [""]
        for titel, key in (("Typografie", "typografie"), ("Farbwelt", "farbwelt"), ("Bildsprache", "bildsprache"),
                           ("Interaktion & Animation", "interaktion_animation"), ("Mobil", "mobil")):
            z += _abschnitt(titel, a.get(key))
    elif e["art"] == "idee":
        z += _abschnitt("Einschaetzung", a.get("einschaetzung"))
        z += _liste("Moeglichkeiten", a.get("moeglichkeiten"))
        z += _liste("Naechste Schritte", a.get("naechste_schritte"))
        z.append(f"Aufwand: **{a.get('aufwand')}** · Nutzen: **{a.get('nutzen')}**\n")

    z += _liste("Staerken", a.get("staerken"))
    z += _liste("Schwaechen", a.get("schwaechen"))
    if a.get("techniken"):
        z += ["## Techniken", ""]
        for t in a["techniken"]:
            zeichen = "✅" if t["bewertung"] == "gut" else "⛔"
            z += [f"### {zeichen} {t['name']} _({t['kategorie']})_", "", t["beschreibung"], "",
                  f"**So bauen wir es nach:** {t['umsetzung']}", "", f"_{t['begruendung']}_", ""]
    z += _abschnitt("So setzen wir es ein", a.get("anwendung_fuer_uns"))
    if a.get("passende_branchen"):
        z.append("Passt zu: " + ", ".join(a["passende_branchen"]) + "\n")
    if bild_md:
        z += ["## Schluesselbilder", ""] + bild_md + [""]

    datei = ordner / "README.md"
    datei.write_text("\n".join(z), encoding="utf-8")
    return datei


def _abschnitt(titel, text):
    return [f"## {titel}", "", text, ""] if text else []


def _liste(titel, punkte):
    return [f"## {titel}", ""] + [f"- {p}" for p in punkte] + [""] if punkte else []


# --------------------------------------------------------------------------- Gesamt-Bibliothek

def schreibe_bibliothek(db: DB, ziel: Path | None = None) -> Path:
    """STIL-BIBLIOTHEK.md: alle Techniken nach Bereich/Kategorie, haeufigste zuerst,
    plus Caption-Presets und die besten Vorlagen."""
    ziel = ziel or (config.BIBLIOTHEK / "STIL-BIBLIOTHEK.md")
    z = ["# Stil-Bibliothek", "",
         f"_Automatisch erzeugt am {datetime.now():%d.%m.%Y %H:%M} aus der Inspo-Inbox. "
         "Zahl in Klammern = wie oft die Technik in geteilter Inspiration auftauchte._", ""]

    alle = db.techniken()
    bereiche = sorted({t["bereich"] for t in alle})
    for bereich in bereiche:
        z += [f"## {bereich.capitalize()}", ""]
        presets = [t for t in alle if t["bereich"] == bereich and t["details_json"]]
        if presets:
            z += ["### Caption-Presets", "", "| Preset | Rezept | Beispiele |", "|---|---|---|"]
            for t in presets:
                z.append(f"| {t['name'].replace('Caption-Preset: ', '')} | {t['umsetzung']} | "
                         f"{_beispiel_links(t)} |")
            z.append("")
        for bewertung, titel in (("gut", "Bewaehrt – nutzen"), ("schlecht", "Anti-Muster – vermeiden")):
            gruppe = [t for t in alle if t["bereich"] == bereich and t["bewertung"] == bewertung
                      and not t["details_json"]]
            if not gruppe:
                continue
            z += [f"### {titel}", ""]
            for kat in sorted({t["kategorie"] for t in gruppe}):
                z += [f"#### {KATEGORIE_TITEL.get(kat, kat)}", ""]
                for t in sorted((t for t in gruppe if t["kategorie"] == kat), key=lambda t: -t["anzahl"]):
                    z.append(f"- **{t['name']}** ({t['anzahl']}x) – {t['beschreibung']}  \n"
                             f"  _Umsetzung:_ {t['umsetzung']}  \n  _Beispiele:_ {_beispiel_links(t)}")
                z.append("")

    beste = db.beste(limit=15)
    if beste:
        z += ["## Top-Vorlagen", "", "| Score | Titel | Bereich | Quelle |", "|---|---|---|---|"]
        for e in beste:
            z.append(f"| {e['score']} | {e['titel']} | {e['bereich']} | {e['url'] or '–'} |")
        z.append("")

    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text("\n".join(z), encoding="utf-8")
    return ziel


def _beispiel_links(t) -> str:
    ids = json.loads(t["beispiele"])[:5]
    return ", ".join(f"#{i}" for i in ids)


# --------------------------------------------------------------------------- Claude-Skill-Export

SKILL_MD = """---
name: stil-bibliothek
description: Unsere gesammelte Stil-Bibliothek aus der Inspo-Inbox - bewaehrte Hooks, Schnitt-Techniken, Caption-Presets (Schrift, Farbe, Position, Animation), Looks, Sounddesign sowie Web-Design-Muster, jeweils mit Nachbau-Anleitung und Anti-Mustern. Immer verwenden, wenn ein Video, Reel, Spot, Skript, Schnittplan, eine Caption-Gestaltung oder eine Website fuer uns oder einen Kunden geplant, geschnitten oder bewertet wird, oder wenn jemand fragt "wie machen wir das", "welcher Stil passt", "welche Schrift/Captions" oder "was haben wir dazu gesammelt".
---

# Stil-Bibliothek

Diese Bibliothek waechst automatisch: Jedes Teammitglied schickt Inspiration in die Inspo-Gruppe,
die Inspo-Inbox analysiert sie und legt die uebertragbaren Techniken hier ab.

## So nutzt du sie

1. Lies `references/STIL-BIBLIOTHEK.md`. Techniken mit hoher Zahl (z.B. "(7x)") sind Muster, die das
   Team immer wieder gut findet - bevorzuge sie.
2. Fuer Captions: Waehle ein Caption-Preset aus der Tabelle und uebernimm das Rezept 1:1
   (Schrift, Gewicht, Farben, Kontur, Position, Woerter je Einblendung, Animation).
3. Vermeide alles unter "Anti-Muster".
4. Brauchst du ein Beispiel im Detail, oeffne den Breakdown `references/eintraege/<nr>-*/README.md`
   (die Nummer steht bei "Beispiele: #<nr>").
5. Schlage bei neuen Projekten 2-3 passende Kombinationen vor (Hook + Schnitt + Caption-Preset + Look)
   und nenne jeweils die Vorlage, aus der sie stammt.
"""


def exportiere_skill(db: DB, ziel: Path) -> Path:
    """Schreibt einen Claude-Skill-Ordner (SKILL.md + references/), den der KI-Mitarbeiter laden kann."""
    ziel = Path(ziel)
    ref = ziel / "references"
    ref.mkdir(parents=True, exist_ok=True)
    (ziel / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    schreibe_bibliothek(db, ref / "STIL-BIBLIOTHEK.md")
    quelle = config.BIBLIOTHEK / "eintraege"
    if quelle.is_dir():
        shutil.copytree(quelle, ref / "eintraege", dirs_exist_ok=True)
    return ziel
