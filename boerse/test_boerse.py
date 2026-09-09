#!/usr/bin/env python3
"""Tests fuer den Boersen-Simulator - ohne Netz, mit gebauten Kursreihen.

Der wichtigste Test ist test_kein_blick_in_die_zukunft. Ein Backtest, der zum
Schlusskurs des Signaltages kauft, rechnet sich systematisch reich. Wenn dieser
Test faellt, sind alle Renditezahlen des Projekts wertlos.

Aufruf: python3 -m unittest boerse.test_boerse -v
"""

import json
import os
import tempfile
import unittest

from . import backtest, indikatoren, motor, portfolio, strategien
from .kurse import Kerze
from .portfolio import Auftrag, Depot


def kerze(datum, schluss, auf=None, hoch=None, tief=None):
    """Kerze mit plausiblen Werten - fehlende Angaben leiten sich vom Schluss ab."""
    auf = schluss if auf is None else auf
    hoch = max(auf, schluss) if hoch is None else hoch
    tief = min(auf, schluss) if tief is None else tief
    return Kerze(datum, auf, hoch, tief, schluss, 1000.0)


def reihe(kurse, start_tag=1):
    """Liste von Schlusskursen in Kerzen mit fortlaufendem Datum uebersetzen."""
    kerzen = []
    for i, kurs in enumerate(kurse):
        tag = start_tag + i
        datum = f"2026-{1 + tag // 28:02d}-{1 + tag % 28:02d}"
        kerzen.append(kerze(datum, kurs))
    return kerzen


def immer(richtung, grund="Test"):
    """Strategie-Attrappe: gibt bei jedem Aufruf dasselbe Signal."""
    def strategie(kerzen, hat_position):
        if richtung == "kaufen" and hat_position:
            return strategien.HALTEN
        if richtung == "verkaufen" and not hat_position:
            return strategien.HALTEN
        return strategien.Signal(richtung, grund)
    return strategie


class TestIndikatoren(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(indikatoren.sma([1, 2, 3, 4, 5], 5), 3.0)
        self.assertEqual(indikatoren.sma([1, 2, 3, 4, 5], 2), 4.5)

    def test_zu_kurze_historie_gibt_none(self):
        # None heisst "keine Aussage" - eine 0 waere ein Kaufsignal aus dem Nichts.
        self.assertIsNone(indikatoren.sma([1, 2], 5))
        self.assertIsNone(indikatoren.rsi([1, 2, 3], 14))
        self.assertIsNone(indikatoren.atr([kerze("2026-01-01", 10)], 14))

    def test_rsi_grenzen(self):
        self.assertEqual(indikatoren.rsi(list(range(1, 30)), 14), 100.0)
        self.assertEqual(indikatoren.rsi(list(range(30, 1, -1)), 14), 0.0)
        # Streng abwechselnd gleich grosse Schritte: der Index muss in der Mitte
        # liegen. Exakt 50 wird er nicht, weil Wilders Glaettung den letzten
        # Schritt hoeher gewichtet - je nachdem, ob er auf- oder abwaerts ging.
        wechsel = [100 + (1 if i % 2 else 0) for i in range(40)]
        self.assertTrue(40.0 < indikatoren.rsi(wechsel, 14) < 60.0)

    def test_atr_kennt_die_kursluecke(self):
        # Schluss 10, naechster Tag eroeffnet und schliesst bei 20: die wahre
        # Spanne ist 10, nicht die Tagesspanne von 0.
        kerzen = [kerze("2026-01-01", 10), kerze("2026-01-02", 20, auf=20)]
        spanne = indikatoren.atr(kerzen, 1)
        self.assertAlmostEqual(spanne, 10.0)

    def test_hoechst_und_tiefst(self):
        kerzen = [kerze("2026-01-01", 10), kerze("2026-01-02", 15), kerze("2026-01-03", 5)]
        self.assertEqual(indikatoren.hoechst(kerzen, 3), 15)
        self.assertEqual(indikatoren.tiefst(kerzen, 3), 5)


class TestStrategien(unittest.TestCase):
    def test_sma_kauft_nur_am_kreuzungstag(self):
        # 60 Tage fallend, dann steil steigend: irgendwann kreuzt SMA20 den SMA50.
        kurse = [100 - i * 0.5 for i in range(60)] + [70 + i * 2.0 for i in range(40)]
        kerzen = reihe(kurse)
        signale = []
        for i in range(51, len(kerzen)):
            signal = strategien.sma_kreuzung(kerzen[:i + 1], False)
            if signal.richtung == "kaufen":
                signale.append(i)
        self.assertEqual(len(signale), 1, "Kaufsignal muss genau einmal ausloesen, nicht taeglich")

    def test_ausbruch_misst_gegen_die_vergangenheit(self):
        # Flach bei 100, dann ein Schluss ueber dem 20-Tage-Hoch.
        kerzen = reihe([100.0] * 30 + [105.0])
        self.assertEqual(strategien.ausbruch(kerzen, False).richtung, "kaufen")
        # Ohne den Ausbruch bleibt es still.
        self.assertEqual(strategien.ausbruch(reihe([100.0] * 31), False).richtung, "halten")

    def test_strategie_ohne_position_verkauft_nicht(self):
        kerzen = reihe([100 - i for i in range(40)])
        for _, funktion in strategien.KATALOG.values():
            self.assertNotEqual(funktion(kerzen, False).richtung, "verkaufen",
                                "ohne Position darf kein Verkaufssignal entstehen")

    def test_unbekannte_strategie_bricht_ab(self):
        with self.assertRaises(SystemExit):
            strategien.waehle("bauchgefuehl")


class TestMotor(unittest.TestCase):
    def setUp(self):
        self.regeln = motor.Regeln(gebuehr=1.0, slippage=0.0, max_positionen=3,
                                   max_anteil=1.0, min_order=10.0, stop_prozent=0.06)

    def test_kein_blick_in_die_zukunft(self):
        """Signal am Schluss von Tag 1, Ausfuehrung zur Eroeffnung von Tag 2."""
        depot = Depot(1000.0, 1000.0)
        tag1 = {"AAA": [kerze("2026-01-01", 100.0)]}
        motor.handelstag(depot, tag1, "2026-01-01", self.regeln, immer("kaufen"))
        self.assertEqual(depot.positionen, {}, "am Signaltag darf noch nichts gekauft sein")
        self.assertEqual(len(depot.auftraege), 1)

        # Tag 2 eroeffnet mit einer Luecke nach oben. Wer zum Schluss von Tag 1
        # gekauft haette, saehe hier einen Gewinn aus dem Nichts.
        tag2 = {"AAA": [kerze("2026-01-01", 100.0), kerze("2026-01-02", 130.0, auf=120.0)]}
        motor.handelstag(depot, tag2, "2026-01-02", self.regeln, immer("kaufen"))
        self.assertEqual(depot.positionen["AAA"].einstand, 120.0,
                         "gekauft wird zur Eroeffnung des Folgetages, nicht zum Vortagsschluss")

    def test_bargeld_bleibt_vollstaendig(self):
        """Kaufen und Verkaufen ohne Kursaenderung kostet genau zwei Gebuehren."""
        depot = Depot(1000.0, 1000.0)
        kerzen = [kerze("2026-01-01", 100.0)]
        motor.handelstag(depot, {"AAA": kerzen}, "2026-01-01", self.regeln, immer("kaufen"))
        kerzen.append(kerze("2026-01-02", 100.0))
        motor.handelstag(depot, {"AAA": kerzen}, "2026-01-02", self.regeln, immer("kaufen"))
        stueck = depot.positionen["AAA"].stueck
        kerzen.append(kerze("2026-01-03", 100.0))
        motor.handelstag(depot, {"AAA": kerzen}, "2026-01-03", self.regeln, immer("verkaufen"))
        kerzen.append(kerze("2026-01-04", 100.0))
        motor.handelstag(depot, {"AAA": kerzen}, "2026-01-04", self.regeln, immer("verkaufen"))

        self.assertEqual(depot.positionen, {})
        self.assertEqual(depot.gebuehren_gesamt, 2.0)
        self.assertAlmostEqual(depot.bargeld, 1000.0 - 2.0, places=2)
        self.assertEqual(stueck, 9, "999 EUR Einsatz bei Kurs 100 reichen fuer 9 Stueck")

    def test_stop_greift_am_tagestief(self):
        depot = Depot(1000.0, 1000.0)
        kerzen = [kerze("2026-01-01", 100.0)]
        motor.handelstag(depot, {"AAA": kerzen}, "2026-01-01", self.regeln, immer("kaufen"))
        kerzen.append(kerze("2026-01-02", 100.0))
        motor.handelstag(depot, {"AAA": kerzen}, "2026-01-02", self.regeln, immer("kaufen"))
        stop = depot.positionen["AAA"].stop
        self.assertAlmostEqual(stop, 94.0)

        # Tag faellt bis 90, schliesst wieder bei 99: der Stop war beruehrt.
        kerzen.append(kerze("2026-01-03", 99.0, auf=99.0, hoch=99.0, tief=90.0))
        motor.handelstag(depot, {"AAA": kerzen}, "2026-01-03", self.regeln, immer("kaufen"))
        self.assertEqual(depot.positionen, {})
        self.assertEqual(depot.historie[-1]["kurs"], 94.0)

    def test_stop_bei_kursluecke_nach_unten(self):
        """Eroeffnet der Kurs unter dem Stop, gibt es den Stop-Preis nicht mehr."""
        depot = Depot(1000.0, 1000.0)
        kerzen = [kerze("2026-01-01", 100.0)]
        motor.handelstag(depot, {"AAA": kerzen}, "2026-01-01", self.regeln, immer("kaufen"))
        kerzen.append(kerze("2026-01-02", 100.0))
        motor.handelstag(depot, {"AAA": kerzen}, "2026-01-02", self.regeln, immer("kaufen"))

        kerzen.append(kerze("2026-01-03", 82.0, auf=80.0, hoch=83.0, tief=79.0))
        motor.handelstag(depot, {"AAA": kerzen}, "2026-01-03", self.regeln, immer("kaufen"))
        self.assertEqual(depot.historie[-1]["kurs"], 80.0,
                         "ausgefuehrt wird zur Eroeffnung, nicht zum unerreichbaren Stop")

    def test_zu_kleine_order_wird_abgelehnt(self):
        regeln = motor.Regeln(gebuehr=1.0, slippage=0.0, max_positionen=3, max_anteil=1.0,
                              min_order=150.0, stop_prozent=0.06)
        depot = Depot(100.0, 100.0)
        kerzen = [kerze("2026-01-01", 50.0)]
        motor.handelstag(depot, {"AAA": kerzen}, "2026-01-01", regeln, immer("kaufen"))
        kerzen.append(kerze("2026-01-02", 50.0))
        ereignisse = motor.handelstag(depot, {"AAA": kerzen}, "2026-01-02", regeln, immer("kaufen"))
        self.assertEqual(depot.positionen, {})
        self.assertTrue(any("Mindestgroesse" in e for e in ereignisse))

    def test_hoechstens_drei_positionen(self):
        regeln = motor.Regeln(gebuehr=1.0, slippage=0.0, max_positionen=2, max_anteil=0.4,
                              min_order=10.0, stop_prozent=0.06)
        depot = Depot(1000.0, 1000.0)
        namen = ["AAA", "BBB", "CCC", "DDD"]
        verlauf = {n: [kerze("2026-01-01", 100.0)] for n in namen}
        motor.handelstag(depot, verlauf, "2026-01-01", regeln, immer("kaufen"))
        for n in namen:
            verlauf[n].append(kerze("2026-01-02", 100.0))
        motor.handelstag(depot, verlauf, "2026-01-02", regeln, immer("kaufen"))
        self.assertEqual(len(depot.positionen), 2)

    def test_auftrag_verfaellt_ohne_kurs(self):
        depot = Depot(1000.0, 1000.0)
        depot.auftraege = [Auftrag("AAA", "kaufen", "Test", "2026-01-01")]
        ereignisse = motor.handelstag(depot, {"BBB": [kerze("2026-01-02", 10.0)]},
                                      "2026-01-02", self.regeln, immer("halten"))
        self.assertTrue(any("verfallen" in e for e in ereignisse))
        self.assertEqual(depot.positionen, {})

    def test_verkauf_macht_platz_fuer_kauf(self):
        """Ein Verkaufssignal gibt den Platz frei, auch wenn es alphabetisch spaeter kommt."""
        regeln = motor.Regeln(gebuehr=1.0, slippage=0.0, max_positionen=1, max_anteil=1.0,
                              min_order=10.0, stop_prozent=0.5)
        depot = Depot(1000.0, 1000.0, strategie="test")
        depot.positionen["ZZZ"] = portfolio.Position("ZZZ", 5, 100.0, "2026-01-01", 50.0)
        depot.bargeld = 500.0

        def strategie(kerzen, hat_position):
            return strategien.Signal("verkaufen" if hat_position else "kaufen", "Test")

        daten = {"AAA": [kerze("2026-01-02", 100.0)], "ZZZ": [kerze("2026-01-02", 100.0)]}
        motor.handelstag(depot, daten, "2026-01-02", regeln, strategie)
        aktionen = {a.kuerzel: a.aktion for a in depot.auftraege}
        self.assertEqual(aktionen, {"AAA": "kaufen", "ZZZ": "verkaufen"})


class TestBacktest(unittest.TestCase):
    def test_seitwaerts_erzeugt_keine_trades(self):
        daten = {"AAA": reihe([100.0] * 200)}
        depot, _ = backtest.laufen(daten, strategien.sma_kreuzung, 1000.0, motor.Regeln())
        self.assertEqual(depot.historie, [], "ohne Bewegung darf keine Strategie handeln")
        self.assertEqual(depot.bargeld, 1000.0)

    def test_trendfolge_verdient_im_trend(self):
        # Erst 80 Tage abwaerts, dann 220 aufwaerts. Der Abwaertsteil ist noetig:
        # ohne ihn liegt der SMA20 von Beginn an ueber dem SMA50 und kreuzt nie -
        # eine Trendfolge steigt in einen laufenden Trend nicht mehr ein.
        daten = {"AAA": reihe([140 - i * 0.5 for i in range(80)]
                              + [100 + i * 0.5 for i in range(220)])}
        depot, im_markt = backtest.laufen(daten, strategien.sma_kreuzung, 1000.0, motor.Regeln())
        zahlen = backtest.kennzahlen(depot, daten, im_markt)
        self.assertGreater(zahlen["endwert"], 1000.0)
        self.assertGreater(zahlen["im_markt"], 30.0)

    def test_kennzahlen_bleiben_ohne_trade_definiert(self):
        daten = {"AAA": reihe([100.0] * 100)}
        depot, im_markt = backtest.laufen(daten, strategien.sma_kreuzung, 1000.0, motor.Regeln())
        zahlen = backtest.kennzahlen(depot, daten, im_markt)
        self.assertEqual(zahlen["trades"], 0)
        self.assertEqual(zahlen["trefferquote"], 0.0)
        self.assertEqual(zahlen["einbruch"], 0.0)

    def test_papiere_mit_unterschiedlichen_handelstagen(self):
        """Ein Feiertag in einem Papier darf den ganzen Lauf nicht kippen."""
        aaa = reihe([100.0 + i for i in range(60)])
        bbb = [k for i, k in enumerate(reihe([50.0 + i for i in range(60)])) if i % 7]
        depot, _ = backtest.laufen({"AAA": aaa, "BBB": bbb}, strategien.sma_kreuzung,
                                   1000.0, motor.Regeln())
        self.assertEqual(len(depot.verlauf), len(backtest.zeitachse({"AAA": aaa, "BBB": bbb})))


class TestZustand(unittest.TestCase):
    def test_speichern_und_laden(self):
        depot = Depot(1000.0, 700.0, start_datum="2026-01-01", strategie="sma")
        depot.positionen["AAA"] = portfolio.Position("AAA", 3, 99.5, "2026-01-01", 93.5, "sma")
        depot.auftraege.append(Auftrag("BBB", "kaufen", "Signal", "2026-01-02"))
        depot.verlauf.append(["2026-01-01", 998.0])

        with tempfile.TemporaryDirectory() as ordner:
            pfad = os.path.join(ordner, "boerse.json")
            portfolio.speichern(depot, pfad)
            with open(pfad, encoding="utf-8") as fh:
                self.assertIn("positionen", json.load(fh))
            zurueck = portfolio.laden(pfad)

        self.assertEqual(zurueck.bargeld, 700.0)
        self.assertEqual(zurueck.positionen["AAA"].stueck, 3)
        self.assertEqual(zurueck.auftraege[0].kuerzel, "BBB")
        self.assertEqual(zurueck.als_dict(), depot.als_dict())

    def test_fehlender_zustand_gibt_none(self):
        self.assertIsNone(portfolio.laden("/nicht/vorhanden/boerse.json"))


class TestVergleich(unittest.TestCase):
    def test_kaufen_und_halten_folgt_dem_kurs(self):
        depot = Depot(1000.0, 1000.0)
        depot.referenz = {"AAA": 100.0}
        # Kurs verdoppelt sich: der Vergleichswert muss deutlich zulegen,
        # gemindert um Gebuehr und Spread des einen Kaufs.
        # 1000 EUR reichen bei Kurs 100,10 (inkl. Spread) fuer 9 Stueck = 900,90.
        # Nach 1 EUR Gebuehr bleiben 98,10 liegen - dieser Rest verdoppelt sich
        # nicht mit. 9 * 200 + 98,10 = 1898,10.
        wert = motor.vergleichswert(depot, {"AAA": 200.0})
        self.assertAlmostEqual(wert, 1898.10, places=2)


if __name__ == "__main__":
    unittest.main()
