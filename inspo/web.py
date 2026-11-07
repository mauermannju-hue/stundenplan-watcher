"""Websites erfassen: Screenshots (Desktop + Handy) und harte Fakten (Schriften, Farben, Ueberschriften)."""
from pathlib import Path

from . import config

# Laeuft im Browser: sammelt, was man auf Screenshots nur schaetzen koennte.
_JS_FAKTEN = r"""
() => {
  const zaehle = (arr) => {
    const m = new Map();
    arr.forEach(x => { if (x) m.set(x, (m.get(x) || 0) + 1); });
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8).map(([wert, n]) => ({wert, n}));
  };
  const sichtbar = [...document.querySelectorAll('body *')].filter(el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && el.childElementCount === 0 && (el.innerText || '').trim();
  }).slice(0, 3000);
  const st = el => getComputedStyle(el);
  const ueber = sel => [...document.querySelectorAll(sel)].map(e => e.innerText.trim()).filter(Boolean).slice(0, 12);
  const kopf = el => { const s = st(el); return `${s.fontFamily.split(',')[0].replace(/["']/g,'')} ${s.fontWeight} ${s.fontSize}`; };
  return {
    titel: document.title,
    beschreibung: (document.querySelector('meta[name="description"]') || {}).content || '',
    sprache: document.documentElement.lang || '',
    h1: ueber('h1'), h2: ueber('h2'), h3: ueber('h3').slice(0, 8),
    schriften: zaehle(sichtbar.map(el => st(el).fontFamily.split(',')[0].replace(/["']/g, '').trim())),
    ueberschrift_stil: [...document.querySelectorAll('h1, h2')].slice(0, 4).map(kopf),
    textfarben: zaehle(sichtbar.map(el => st(el).color)),
    hintergruende: zaehle([...document.querySelectorAll('body, body *')].slice(0, 3000)
                     .map(el => st(el).backgroundColor).filter(c => c !== 'rgba(0, 0, 0, 0)')),
    buttons: [...document.querySelectorAll('a, button')].filter(e => {
        const s = st(e); return s.backgroundColor !== 'rgba(0, 0, 0, 0)' && e.innerText.trim().length < 40;
      }).map(e => e.innerText.trim()).filter(Boolean).slice(0, 10),
    abschnitte: document.querySelectorAll('section, main > div, [class*="section"]').length,
    seitenhoehe: document.documentElement.scrollHeight,
    hat_video: !!document.querySelector('video'),
    animationen: [...document.querySelectorAll('*')].slice(0, 3000)
                   .filter(e => st(e).animationName !== 'none').length,
  };
}
"""


def erfasse(url: str, ziel: Path, desktop_bilder: int = 5, handy_bilder: int = 3) -> tuple[dict, list[tuple[str, Path]]]:
    """Rueckgabe: (Fakten, [(Beschriftung, Bildpfad), ...])."""
    from playwright.sync_api import sync_playwright  # erst hier: Playwright ist optional

    ziel.mkdir(parents=True, exist_ok=True)
    bilder: list[tuple[str, Path]] = []
    with sync_playwright() as pw:
        start = {"executable_path": config.CHROMIUM} if config.CHROMIUM else {}
        browser = pw.chromium.launch(**start)
        try:
            for geraet, viewport, anzahl, mobil in (("Desktop", {"width": 1440, "height": 900}, desktop_bilder, False),
                                                    ("Handy", {"width": 390, "height": 844}, handy_bilder, True)):
                ctx = browser.new_context(viewport=viewport, is_mobile=mobil, has_touch=mobil,
                                          device_scale_factor=1, locale="de-DE")
                seite = ctx.new_page()
                seite.goto(url, wait_until="domcontentloaded", timeout=45000)
                try:
                    seite.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass  # Seiten mit Dauer-Polling werden nie "idle"
                _cookie_banner_weg(seite)
                if geraet == "Desktop":
                    fakten = seite.evaluate(_JS_FAKTEN)
                hoehe = seite.evaluate("document.documentElement.scrollHeight")
                for i in range(anzahl):
                    y = i * viewport["height"]
                    if i and y >= hoehe:
                        break
                    seite.evaluate(f"window.scrollTo(0, {y})")
                    seite.wait_for_timeout(700)  # Lazy-Loading + Scroll-Animationen
                    datei = ziel / f"{geraet.lower()}_{i}.jpg"
                    seite.screenshot(path=str(datei), type="jpeg", quality=70)
                    bilder.append((f"{geraet}, Bildschirm {i + 1}", datei))
                ctx.close()
        finally:
            browser.close()
    return fakten, bilder


def _cookie_banner_weg(seite) -> None:
    for text in ("Alle akzeptieren", "Akzeptieren", "Accept all", "Accept", "Zustimmen", "Einverstanden", "OK"):
        try:
            knopf = seite.get_by_role("button", name=text, exact=False).first
            if knopf.is_visible(timeout=500):
                knopf.click(timeout=1500)
                seite.wait_for_timeout(500)
                return
        except Exception:
            continue
