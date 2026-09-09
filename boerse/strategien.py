#!/usr/bin/env python3
"""Die Handelsstrategien.

Jede Strategie ist eine Funktion, die den bisherigen Kursverlauf sieht - und
zwar nur bis einschliesslich heute, nie einen Tag weiter. Sie liefert ein
Signal mit Begruendung. Ausgefuehrt wird erst am naechsten Handelstag zur
Eroeffnung; siehe motor.py. Genau das trennt einen ehrlichen Test von einem,
der die Zukunft kennt.

Drei Familien, die sich fundamental widersprechen - das ist Absicht:
  sma       Trendfolge   "was steigt, steigt weiter"
  rsi       Rueckkehr    "was zu weit faellt, holt auf"
  ausbruch  Momentum     "neues Hoch heisst Aufbruch"

Welche davon funktioniert, entscheidet nicht die Ueberzeugung, sondern der
Backtest - und auch der nur mit Vorbehalt.
"""

from dataclasses import dataclass, field

from . import indikatoren


@dataclass
class Signal:
    """Was die Strategie will, und warum."""
    richtung: str                      # "kaufen", "verkaufen" oder "halten"
    grund: str = ""
    kennzahlen: dict = field(default_factory=dict)


HALTEN = Signal("halten")


def sma_kreuzung(kerzen, hat_position, kurz=20, lang=50):
    """Trendfolge: kaufen, wenn der kurze Schnitt den langen von unten kreuzt.

    Der Vergleich mit gestern ist der Kern - gekauft wird am Kreuzungstag,
    nicht an jedem Tag, an dem der kurze Schnitt zufaellig oben liegt.
    """
    if len(kerzen) < lang + 1:
        return HALTEN
    schluss = [k.schluss for k in kerzen]
    heute_kurz, heute_lang = indikatoren.sma(schluss, kurz), indikatoren.sma(schluss, lang)
    gestern_kurz = indikatoren.sma(schluss[:-1], kurz)
    gestern_lang = indikatoren.sma(schluss[:-1], lang)
    if None in (heute_kurz, heute_lang, gestern_kurz, gestern_lang):
        return HALTEN

    zahlen = {f"SMA{kurz}": round(heute_kurz, 2), f"SMA{lang}": round(heute_lang, 2)}
    if not hat_position and gestern_kurz <= gestern_lang and heute_kurz > heute_lang:
        return Signal("kaufen", f"SMA{kurz} kreuzt SMA{lang} von unten", zahlen)
    if hat_position and gestern_kurz >= gestern_lang and heute_kurz < heute_lang:
        return Signal("verkaufen", f"SMA{kurz} faellt unter SMA{lang}", zahlen)
    return HALTEN


def rsi_rueckkehr(kerzen, hat_position, periode=14, einstieg=30.0, ausstieg=55.0):
    """Rueckkehr zum Mittelwert: im Ausverkauf kaufen, bei Erholung abgeben.

    Die Gegenposition zur Trendfolge. In Seitwaertsphasen ueberlegen, in einem
    Abwaertstrend gefaehrlich: "ueberverkauft" kann monatelang ueberverkaufter
    werden. Der Stop-Loss ist hier kein Beiwerk, sondern die Reissleine.
    """
    if len(kerzen) < periode + 2:
        return HALTEN
    schluss = [k.schluss for k in kerzen]
    heute = indikatoren.rsi(schluss, periode)
    gestern = indikatoren.rsi(schluss[:-1], periode)
    if heute is None or gestern is None:
        return HALTEN

    zahlen = {f"RSI{periode}": round(heute, 1)}
    if not hat_position and gestern < einstieg <= heute:
        return Signal("kaufen", f"RSI dreht bei {heute:.0f} aus dem ueberverkauften Bereich",
                      zahlen)
    if hat_position and heute >= ausstieg:
        return Signal("verkaufen", f"RSI zurueck bei {heute:.0f}", zahlen)
    return HALTEN


def ausbruch(kerzen, hat_position, fenster=20, ausstieg_fenster=10):
    """Momentum nach Donchian: kaufen am neuen Hoch, raus am juengsten Tief.

    Der Vergleich laeuft gegen die Kerzen *vor* heute - sonst waere das heutige
    Hoch Teil seines eigenen Massstabs und das Signal koennte nie ausloesen.
    """
    if len(kerzen) < fenster + 2:
        return HALTEN
    heute = kerzen[-1]
    vorher = kerzen[:-1]
    obere = indikatoren.hoechst(vorher, fenster)
    untere = indikatoren.tiefst(vorher, ausstieg_fenster)
    if obere is None or untere is None:
        return HALTEN

    zahlen = {f"Hoch{fenster}": round(obere, 2), f"Tief{ausstieg_fenster}": round(untere, 2)}
    if not hat_position and heute.schluss > obere:
        return Signal("kaufen", f"Schluss {heute.schluss:.2f} ueber {fenster}-Tage-Hoch "
                                f"{obere:.2f}", zahlen)
    if hat_position and heute.schluss < untere:
        return Signal("verkaufen", f"Schluss {heute.schluss:.2f} unter {ausstieg_fenster}-Tage-"
                                   f"Tief {untere:.2f}", zahlen)
    return HALTEN


KATALOG = {
    "sma": ("Trendfolge (SMA 20/50)", sma_kreuzung),
    "rsi": ("Rueckkehr zum Mittelwert (RSI 14)", rsi_rueckkehr),
    "ausbruch": ("Ausbruch (Donchian 20/10)", ausbruch),
}


def waehle(name):
    if name not in KATALOG:
        bekannt = ", ".join(sorted(KATALOG))
        raise SystemExit(f"Unbekannte Strategie {name!r}. Bekannt sind: {bekannt}")
    return KATALOG[name]
