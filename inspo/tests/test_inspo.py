"""Offline-Tests: kein Telegram, kein Instagram, keine echten Claude-Aufrufe.
Ausfuehren:  python -m unittest discover -s inspo/tests -v   (aus dem Repo-Wurzelordner)"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="inspo-test-"))
os.environ["INSPO_DATEN"] = str(TMP / "daten")
os.environ["INSPO_TG_CHATS"] = ""
os.environ["INSPO_SCHWELLE"] = "6"

from inspo import analyse, bibliothek, config, ingest, video  # noqa: E402
from inspo.bot import InspoBot  # noqa: E402
from inspo.db import DB  # noqa: E402
from inspo.verarbeitung import arbeite_warteschlange, verarbeite  # noqa: E402

HAT_FFMPEG = video.ffmpeg_verfuegbar()


def testvideo(pfad: Path) -> Path:
    """6 s Hochkant-Clip: drei Farbflaechen (= 2 harte Schnitte) + Sinuston."""
    if pfad.exists():
        return pfad
    pfad.parent.mkdir(parents=True, exist_ok=True)
    teile = [f"color=c={c}:s=540x960:d=2:r=30" for c in ("red", "blue", "green")]
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for t in teile:
        cmd += ["-f", "lavfi", "-i", t]
    cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(pfad)]
    subprocess.run(cmd, check=True)
    return pfad


def fake_breakdown(score=8, urteil="uebernehmen", technik="Wort-fuer-Wort Pop-Captions"):
    return analyse.VideoBreakdown(
        titel="Handwerker Vorher/Nachher mit Speed Ramp", zusammenfassung="Starker Hook, sauberer Payoff.",
        bereich="videografie", format="Vorher/Nachher", hook="Kaputtes Bad + Text 'Das hier kostet 20k?'",
        hook_bewertung="sehr stark", struktur=[analyse.Szene(zeit="0:00-0:02", inhalt="Vorher", zweck="Hook")],
        tempo_und_schnitt="2 Schnitte in 6 s", captions=analyse.CaptionStil(
            vorhanden=True, schrift_vermutung="Montserrat", alternativen=["Poppins"],
            gewicht_und_schreibweise="Black, GROSS", farben="#FFFFFF, Highlight #FFD400",
            kontur_schatten_box="schwarze Kontur 6px", position="Mitte 60%", woerter_pro_einblendung="1-3",
            animation="Pop-in 120ms", emojis_grafiken="keine"),
        farbe_und_look="warm", kamera_und_licht="Handy, Gimbal", ton_musik_sfx="Whoosh auf Schnitt",
        cta="Folgen fuer Teil 2", staerken=["Hook"], schwaechen=["CTA zu leise"],
        techniken=[analyse.Technik(kategorie="captions", name=technik, beschreibung="Ein Wort pro Einblendung",
                                   umsetzung="CapCut: Auto-Captions, Stil 'Pop'", bewertung="gut", begruendung="lesbar"),
                   analyse.Technik(kategorie="cta", name="Leiser CTA ohne Text", beschreibung="CTA nur gesprochen",
                                   umsetzung="-", bewertung="schlecht", begruendung="geht unter")],
        anwendung_fuer_uns="Fuer Badsanierer-Kunden 1:1 uebertragbar.", passende_branchen=["Handwerk"],
        score=score, urteil=urteil, tags=["vorher-nachher", "handwerk"])


# =========================================================================== ingest

class TestIngest(unittest.TestCase):
    def test_urls_und_normalisierung(self):
        text = ("Schau mal https://www.instagram.com/reel/ABC123/?igsh=xyz krass! und "
                "https://vm.tiktok.com/ZMabc/ sowie https://example.com/seite?utm_source=x&id=5.")
        urls = ingest.finde_urls(text)
        self.assertEqual(urls, ["https://instagram.com/reel/ABC123/", "https://vm.tiktok.com/ZMabc/",
                                "https://example.com/seite?id=5"])
        self.assertEqual(ingest.text_ohne_urls(text), "Schau mal krass! und sowie")

    def test_art(self):
        self.assertEqual(ingest.art_von("https://instagram.com/reel/ABC/"), "video")
        self.assertEqual(ingest.art_von("https://youtube.com/shorts/xyz"), "video")
        self.assertEqual(ingest.art_von("https://youtu.be/xyz"), "video")
        self.assertEqual(ingest.art_von("https://instagram.com/somebrand/"), "website")
        self.assertEqual(ingest.art_von("https://stripe.com/de"), "website")

    def test_whatsapp_export(self):
        datei = TMP / "wa.txt"
        datei.write_text("06.11.26, 21:14 - Julian: https://www.instagram.com/reel/AAA/ der Hook!\n"
                         "06.11.26, 21:15 - Max: mega\nzweite Zeile https://site.de\n"
                         "07.11.26, 08:00 - Max hat die Gruppe verlassen\n"
                         "[07.11.26, 09:01:02] Lisa: https://vm.tiktok.com/ZZZ/\n", encoding="utf-8")
        n = ingest.lese_export(datei)
        self.assertEqual([m["absender"] for m in n], ["Julian", "Max", "Lisa"])
        self.assertIn("https://site.de", n[1]["text"])
        self.assertEqual(n[0]["zeit"], "2026-11-06T21:14:00")

    def test_telegram_export(self):
        datei = TMP / "result.json"
        datei.write_text(json.dumps({"messages": [
            {"type": "message", "date": "2026-11-01T10:00:00", "from": "Julian",
             "text": ["Guck: ", {"type": "text_link", "text": "das hier", "href": "https://tiktok.com/@a/video/1"}]},
            {"type": "service", "action": "join"}]}), encoding="utf-8")
        n = ingest.lese_export(datei)
        self.assertEqual(len(n), 1)
        self.assertIn("https://tiktok.com/@a/video/1", n[0]["text"])


# =========================================================================== video

@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class TestVideo(unittest.TestCase):
    def test_messung_und_bilder(self):
        pfad = testvideo(TMP / "clip.mp4")
        meta = video.metadaten(pfad)
        self.assertEqual(meta["format"], "hochkant")
        self.assertTrue(meta["hat_ton"])
        self.assertAlmostEqual(meta["dauer"], 6, delta=0.2)
        cuts = video.schnitte(pfad)
        self.assertEqual(len(cuts), 2, cuts)
        self.assertAlmostEqual(cuts[0], 2, delta=0.1)
        stat = video.schnitt_statistik(meta["dauer"], cuts)
        self.assertEqual(stat["einstellungen"], 3)
        zeiten = video.zeitpunkte(meta["dauer"], cuts, 6)
        self.assertEqual(len(zeiten), 6)
        self.assertLess(zeiten[0], 0.5)  # Hook-Bild
        bilder = video.bilder(pfad, zeiten, TMP / "bilder")
        self.assertEqual(len(bilder), 6)

    def test_zeitpunkte_duennt_aus(self):
        cuts = [i * 0.5 for i in range(1, 60)]
        self.assertEqual(len(video.zeitpunkte(30, cuts, 8)), 8)


# =========================================================================== Pipeline

@unittest.skipUnless(HAT_FFMPEG, "ffmpeg fehlt")
class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.db = DB(TMP / f"{self._testMethodName}.db")
        self.clip = testvideo(TMP / "clip.mp4")
        self.orig = (analyse.triage, analyse.video_breakdown)
        self.aufrufe = []

    def tearDown(self):
        analyse.triage, analyse.video_breakdown = self.orig

    def _fakes(self, relevanz, breakdown=None):
        def triage(**kw):
            self.aufrufe.append(("triage", len(kw["bilder"])))
            return analyse.Triage(relevanz=relevanz, begruendung="passt", bereich="videografie",
                                  kurzbeschreibung="Bad-Sanierung Reel")

        def tief(**kw):
            self.aufrufe.append(("breakdown", len(kw["bilder"]), kw["bibliothek_kurz"]))
            return breakdown or fake_breakdown()
        analyse.triage, analyse.video_breakdown = triage, tief

    def test_relevantes_video_landet_in_bibliothek(self):
        self._fakes(relevanz=8)
        eid, _ = self.db.neuer_eintrag(art="video", quelle="test", datei=str(self.clip), notiz="Hook ist krass")
        r = verarbeite(self.db, eid)
        self.assertEqual(r["status"], "fertig", r)
        self.assertEqual([a[0] for a in self.aufrufe], ["triage", "breakdown"])
        self.assertEqual(self.aufrufe[0][1], config.BILDER_TRIAGE)
        e = self.db.eintrag(eid)
        self.assertEqual(e["urteil"], "uebernehmen")
        self.assertEqual(json.loads(e["meta_json"])["schnitte"], 2)
        self.assertTrue(Path(e["markdown"]).exists())
        md = Path(e["markdown"]).read_text()
        self.assertIn("Wort-fuer-Wort Pop-Captions", md)
        self.assertIn("![Bild bei", md)
        namen = {t["name"]: t for t in self.db.techniken()}
        self.assertIn("Leiser CTA ohne Text", namen)
        self.assertEqual(namen["Leiser CTA ohne Text"]["bewertung"], "schlecht")
        self.assertTrue(any(n.startswith("Caption-Preset") for n in namen))
        self.assertIn("Fuer uns", r["text"])

        # zweites Video mit gleicher Technik (anders geschrieben) -> Zaehler statt Duplikat
        self._fakes(relevanz=9, breakdown=fake_breakdown(technik="Wort für Wort Pop Captions"))
        eid2, _ = self.db.neuer_eintrag(art="video", quelle="test", datei=str(self.clip))
        verarbeite(self.db, eid2)
        self.assertIn("Wort-fuer-Wort Pop-Captions", self.aufrufe[-1][2])  # Bibliothek im Prompt
        t = [t for t in self.db.techniken() if t["kategorie"] == "captions" and not t["details_json"]]
        # "fuer" und "für" ergeben unterschiedliche Schluessel - das ist ok, beide Schreibweisen zaehlen getrennt;
        # identische Namen muessen zusammengefuehrt werden:
        self._fakes(relevanz=9)
        eid3, _ = self.db.neuer_eintrag(art="video", quelle="test", datei=str(self.clip))
        verarbeite(self.db, eid3)
        t = {x["name"]: x for x in self.db.techniken()}
        self.assertEqual(t["Wort-fuer-Wort Pop-Captions"]["anzahl"], 2)

        datei = bibliothek.schreibe_bibliothek(self.db)
        inhalt = datei.read_text()
        self.assertIn("Caption-Presets", inhalt)
        self.assertIn("Anti-Muster", inhalt)
        skill = bibliothek.exportiere_skill(self.db, TMP / "skill")
        self.assertTrue((skill / "SKILL.md").read_text().startswith("---\nname: stil-bibliothek"))
        self.assertTrue((skill / "references" / "STIL-BIBLIOTHEK.md").exists())

    def test_irrelevantes_video_wird_vorgefiltert(self):
        self._fakes(relevanz=3)
        eid, _ = self.db.neuer_eintrag(art="video", quelle="test", datei=str(self.clip))
        r = verarbeite(self.db, eid)
        self.assertEqual(r["status"], "verworfen")
        self.assertEqual([a[0] for a in self.aufrufe], ["triage"])
        self.assertIn("/analyse", r["text"])
        # erzwingen -> voller Breakdown trotz niedriger Relevanz
        r = verarbeite(self.db, eid, erzwingen=True)
        self.assertEqual(r["status"], "fertig")

    def test_fehler_blockiert_warteschlange_nicht(self):
        self._fakes(relevanz=8)
        kaputt, _ = self.db.neuer_eintrag(art="video", quelle="test", datei=str(TMP / "gibtsnicht.mp4"))
        gut, _ = self.db.neuer_eintrag(art="video", quelle="test", datei=str(self.clip))
        ergebnisse = {}
        arbeite_warteschlange(self.db, rueckmeldung=lambda e, r: ergebnisse.update({e["id"]: r["status"]}))
        self.assertEqual(ergebnisse, {kaputt: "fehler", gut: "fertig"})

    def test_doppelter_link(self):
        a, neu1 = self.db.neuer_eintrag(art="video", quelle="t", url="https://instagram.com/reel/X/", notiz="eins")
        b, neu2 = self.db.neuer_eintrag(art="video", quelle="t", url="https://instagram.com/reel/X/", notiz="zwei")
        self.assertEqual((a, neu1, neu2), (b, True, False))
        e = self.db.eintrag(a)
        self.assertEqual(e["erwaehnungen"], 2)
        self.assertEqual(e["notiz"], "eins\nzwei")


# =========================================================================== Claude-Request

class _FakeAnthropic(BaseHTTPRequestHandler):
    anfragen = []

    def do_POST(self):
        laenge = int(self.headers["content-length"])
        _FakeAnthropic.anfragen.append({"pfad": self.path, "header": dict(self.headers),
                                        "body": json.loads(self.rfile.read(laenge))})
        antwort = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                   "content": [{"type": "text", "text": json.dumps(
                       {"relevanz": 7, "begruendung": "gut", "bereich": "videografie",
                        "kurzbeschreibung": "Test"})}],
                   "stop_reason": "end_turn", "stop_sequence": None,
                   "usage": {"input_tokens": 10, "output_tokens": 5}}
        daten = json.dumps(antwort).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(daten)))
        self.end_headers()
        self.wfile.write(daten)

    def log_message(self, *a):
        pass


class TestClaudeAnfrage(unittest.TestCase):
    """Prueft die Form der API-Anfrage gegen einen lokalen Fake-Server."""

    def test_triage_anfrage(self):
        import anthropic
        server = HTTPServer(("127.0.0.1", 0), _FakeAnthropic)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        alt = analyse._client
        analyse._client = anthropic.Anthropic(api_key="test", base_url=f"http://127.0.0.1:{server.server_port}",
                                              http_client=anthropic.DefaultHttpxClient(trust_env=False),
                                              max_retries=0)
        try:
            bild = TMP / "mini.jpg"
            if HAT_FFMPEG:
                subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64",
                                "-frames:v", "1", str(bild)], check=True)
            else:
                bild.write_bytes(b"\xff\xd8\xff\xd9")
            t = analyse.triage(url="https://instagram.com/reel/X/", notiz="krass", meta={"dauer": 12},
                               bilder=[("Bild bei 0.3s", bild)], bibliothek_kurz="+ [videografie/hook] X (2x)")
        finally:
            analyse._client = alt
            server.shutdown()
        self.assertEqual(t.relevanz, 7)
        req = _FakeAnthropic.anfragen[-1]
        body = req["body"]
        self.assertEqual(body["model"], config.MODELL_TRIAGE)
        self.assertEqual(body["output_config"]["effort"], "low")
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(body["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", req["header"].get("anthropic-beta", ""))
        self.assertNotIn("thinking", body)
        self.assertEqual(body["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertIn("Bereits bekannte Techniken", body["system"][1]["text"])
        typen = [b["type"] for b in body["messages"][0]["content"]]
        self.assertEqual(typen, ["text", "text", "image"])


# =========================================================================== Bot

class FakeTelegram:
    def __init__(self):
        self.gesendet, self.reaktionen, self.dateien = [], [], []

    def sende(self, chat_id, text, antwort_auf=None):
        self.gesendet.append((chat_id, text))

    def reagiere(self, chat_id, message_id, emoji):
        self.reaktionen.append((message_id, emoji))

    def sende_datei(self, chat_id, pfad, caption=""):
        self.dateien.append(pfad)

    def lade_datei(self, file_id, ziel):
        raise AssertionError("nicht erwartet")


def nachricht(text, chat_id=-100, typ="supergroup", mid=1, **extra):
    m = {"message_id": mid, "chat": {"id": chat_id, "type": typ, "title": "Inspo"},
         "from": {"first_name": "Julian", "username": "julian"}, "text": text}
    m.update(extra)
    return {"update_id": mid, "message": m}


class TestBot(unittest.TestCase):
    def setUp(self):
        self.db = DB(TMP / f"bot_{self._testMethodName}.db")
        self.tg = FakeTelegram()
        self.bot = InspoBot(self.db, self.tg)
        self.bot.benutzername = "inspo_bot"

    def test_link_in_gruppe(self):
        self.bot.verarbeite_update(nachricht("Der Hook 🔥 https://www.instagram.com/reel/QQQ/?igsh=1"))
        e = self.db.eintrag(1)
        self.assertEqual((e["art"], e["url"], e["notiz"], e["absender"]),
                         ("video", "https://instagram.com/reel/QQQ/", "Der Hook 🔥", "Julian"))
        self.assertEqual(self.tg.reaktionen, [(1, "👀")])
        self.assertEqual(self.tg.gesendet, [])  # Gruppe bleibt ruhig
        self.assertTrue(self.bot.signal.is_set())

    def test_versteckter_link(self):
        self.bot.verarbeite_update(nachricht("schaut euch das an", entities=[
            {"type": "text_link", "offset": 0, "length": 5, "url": "https://linear.app"}]))
        self.assertEqual(self.db.eintrag(1)["art"], "website")

    def test_gespraech_wird_ignoriert(self):
        self.bot.verarbeite_update(nachricht("haha ja voll"))
        self.assertIsNone(self.db.eintrag(1))
        self.assertEqual(self.tg.reaktionen, [])

    def test_idee_im_privatchat(self):
        self.bot.verarbeite_update(nachricht("Idee: Kunden-Testimonials als POV-Reel", chat_id=42, typ="private"))
        e = self.db.eintrag(1)
        self.assertEqual(e["art"], "idee")
        self.assertIn("Warteschlange", self.tg.gesendet[-1][1])

    def test_befehle(self):
        self.bot.verarbeite_update(nachricht("/idee@inspo_bot Reels mit KI-B-Roll fuer Autohaus"))
        self.assertEqual(self.db.eintrag(1)["art"], "idee")
        self.bot.verarbeite_update(nachricht("/status", mid=2))
        self.assertIn("neu: 1", self.tg.gesendet[-1][1])
        self.bot.verarbeite_update(nachricht("/id", mid=3))
        self.assertIn("-100", self.tg.gesendet[-1][1])
        self.bot.verarbeite_update(nachricht("/bibliothek", mid=4))
        self.assertTrue(self.tg.dateien and self.tg.dateien[-1].name == "STIL-BIBLIOTHEK.md")

    def test_fremder_chat(self):
        alt = config.TG_CHATS
        config.TG_CHATS = {"-100"}
        try:
            self.bot.verarbeite_update(nachricht("https://x.de", chat_id=-999))
            self.assertIsNone(self.db.eintrag(1))
        finally:
            config.TG_CHATS = alt


# =========================================================================== Website (optional)

class TestWeb(unittest.TestCase):
    def test_erfassen(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("playwright fehlt")
        seite = TMP / "seite.html"
        seite.write_text("""<html lang="de"><head><title>Studio</title>
            <style>body{font-family:Georgia;color:#111} h1{font-family:Arial;font-size:64px}
            .b{background:#ff5500;color:#fff;padding:12px}</style></head>
            <body><h1>Wir machen Reels</h1><h2>Leistungen</h2><p>Text</p>
            <a class="b" href="#">Termin buchen</a><div style="height:3000px"></div></body></html>""")
        if not config.CHROMIUM and Path("/opt/pw-browsers/chromium").exists():
            config.CHROMIUM = "/opt/pw-browsers/chromium"  # vorinstallierter Browser (Cloud-Umgebung)
        from inspo import web
        try:
            fakten, bilder = web.erfasse(seite.as_uri(), TMP / "web", desktop_bilder=2, handy_bilder=1)
        except Exception as exc:  # Browser nicht installiert
            self.skipTest(f"Browser nicht startbar: {exc}")
        self.assertEqual(fakten["h1"], ["Wir machen Reels"])
        self.assertIn("Termin buchen", fakten["buttons"])
        self.assertTrue(any(f["wert"] == "Georgia" for f in fakten["schriften"]))
        self.assertEqual(len(bilder), 3)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
