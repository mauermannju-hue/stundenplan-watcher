#!/usr/bin/env python3
"""Die ueblichen Kennzahlen der technischen Analyse - bewusst zu Fuss.

Jede Funktion liefert den Wert fuer den *letzten* Tag der uebergebenen Reihe
oder None, wenn die Historie dafuer noch zu kurz ist. None heisst immer:
"noch keine Aussage moeglich" - nie "null".
"""


def sma(werte, n):
    """Einfacher gleitender Durchschnitt der letzten n Werte."""
    if n <= 0 or len(werte) < n:
        return None
    return sum(werte[-n:]) / n


def ema(werte, n):
    """Exponentiell gewichteter Durchschnitt, Startwert ist der erste SMA."""
    if n <= 0 or len(werte) < n:
        return None
    faktor = 2.0 / (n + 1)
    schnitt = sum(werte[:n]) / n
    for wert in werte[n:]:
        schnitt = wert * faktor + schnitt * (1 - faktor)
    return schnitt


def rsi(werte, periode=14):
    """Relative Strength Index nach Wilder: 0 = nur Verluste, 100 = nur Gewinne.

    Unter 30 gilt als ueberverkauft, ueber 70 als ueberkauft. Das ist eine
    Konvention, kein Naturgesetz.
    """
    if len(werte) < periode + 1:
        return None
    gewinne, verluste = [], []
    for vorher, jetzt in zip(werte, werte[1:]):
        differenz = jetzt - vorher
        gewinne.append(max(differenz, 0.0))
        verluste.append(max(-differenz, 0.0))
    schnitt_g = sum(gewinne[:periode]) / periode
    schnitt_v = sum(verluste[:periode]) / periode
    for i in range(periode, len(gewinne)):
        schnitt_g = (schnitt_g * (periode - 1) + gewinne[i]) / periode
        schnitt_v = (schnitt_v * (periode - 1) + verluste[i]) / periode
    if schnitt_v == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + schnitt_g / schnitt_v)


def atr(kerzen, periode=14):
    """Average True Range - wie weit sich der Kurs an einem Tag ueblich bewegt.

    Nuetzlich fuer Stops: ein Stop enger als die uebliche Tagesschwankung wird
    vom Rauschen ausgeloest, nicht von einer Trendwende.
    """
    if len(kerzen) < periode + 1:
        return None
    spannen = []
    for vorher, jetzt in zip(kerzen, kerzen[1:]):
        spannen.append(max(
            jetzt.hoch - jetzt.tief,
            abs(jetzt.hoch - vorher.schluss),
            abs(jetzt.tief - vorher.schluss),
        ))
    schnitt = sum(spannen[:periode]) / periode
    for spanne in spannen[periode:]:
        schnitt = (schnitt * (periode - 1) + spanne) / periode
    return schnitt


def hoechst(kerzen, n):
    """Hoechstes Hoch der letzten n Kerzen."""
    if len(kerzen) < n or n <= 0:
        return None
    return max(k.hoch for k in kerzen[-n:])


def tiefst(kerzen, n):
    """Tiefstes Tief der letzten n Kerzen."""
    if len(kerzen) < n or n <= 0:
        return None
    return min(k.tief for k in kerzen[-n:])
