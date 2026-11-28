"""Renders PNG / ICO / GIF / WebP assets from the SVG sources and the real splash code.

Needs: playwright, pillow  (uses the Microsoft Edge already on Windows; no browser download)
    python tools/render_assets.py
Run tools/build_logo.py first if you changed the logo.
"""
import json, math, re, tempfile
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
LOGO, PNG, FAV, GIF = ROOT / "logo", ROOT / "png", ROOT / "favicon", ROOT / "gif"
for d in (PNG, FAV, GIF):
    d.mkdir(exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="compass-render-"))
PAPER = "#F7F4EE"
FPS = 15


def uri(p):
    return Path(p).resolve().as_uri()


def shot_svg(page, svg_name, out, width, height=None, bg=None, pad_pct=0, canvas=None):
    """Render logo/<svg_name> at width x height into out (transparent unless bg is given).
    canvas=(w,h) draws the svg centred inside a larger canvas, scaled to pad_pct of it."""
    cw, ch = canvas or (width, height or width)
    iw = width if not canvas else round(cw * pad_pct)
    html = TMP / (out.stem + ".html")
    html.write_text(
        f"<!doctype html><meta charset=utf-8><style>html,body{{margin:0;height:100%;background:{bg or 'transparent'}}}"
        f"body{{display:grid;place-items:center}}img{{width:{iw}px;display:block}}</style>"
        f"<img src='{uri(LOGO / svg_name)}'>")
    page.set_viewport_size({"width": cw, "height": ch})
    page.goto(uri(html))
    page.wait_for_load_state("load")
    page.screenshot(path=str(out), omit_background=bg is None)


def lockup_height(w):
    return round(w * 180 / 545)


def static_assets(page):
    for s in (64, 128, 256, 512, 1024):
        shot_svg(page, "mark.svg", PNG / f"mark-{s}.png", s)
    for s in (16, 32, 48):
        shot_svg(page, "mark-small.svg", PNG / f"mark-small-{s}.png", s)
    shot_svg(page, "mark-reversed.svg", PNG / "mark-reversed-512.png", 512)
    for w in (600, 1200, 2400):
        shot_svg(page, "lockup.svg", PNG / f"lockup-{w}.png", w, lockup_height(w))
    shot_svg(page, "lockup-reversed.svg", PNG / "lockup-reversed-1200.png", 1200, lockup_height(1200))

    # favicons
    for s in (16, 32, 48):
        shot_svg(page, "favicon.svg", FAV / f"favicon-{s}.png", s)
    shot_svg(page, "favicon.svg", TMP / "ico256.png", 256)
    Image.open(TMP / "ico256.png").convert("RGBA").save(
        FAV / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    # app icons: full-bleed paper, mark inset
    shot_svg(page, "mark.svg", FAV / "apple-touch-icon.png", 180, bg=PAPER, canvas=(180, 180), pad_pct=.70)
    shot_svg(page, "mark.svg", FAV / "icon-192.png", 192, bg=PAPER, canvas=(192, 192), pad_pct=.70)
    shot_svg(page, "mark.svg", FAV / "icon-512.png", 512, bg=PAPER, canvas=(512, 512), pad_pct=.70)
    shot_svg(page, "mark.svg", FAV / "icon-maskable-512.png", 512, bg=PAPER, canvas=(512, 512), pad_pct=.54)
    (FAV / "site.webmanifest").write_text(json.dumps({
        "name": "Compass AI", "short_name": "Compass AI",
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
        "theme_color": PAPER, "background_color": PAPER, "display": "standalone"}, indent=2), encoding="utf-8")


def palette_quantize(frames, colors=128):
    """One shared palette for every frame -> no flicker between frames."""
    sample = frames[:: max(1, len(frames) // 10)]
    w, h = sample[0].size
    sheet = Image.new("RGB", (w, h * len(sample)))
    for i, f in enumerate(sample):
        sheet.paste(f, (0, i * h))
    pal = sheet.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    return [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]


def save_gif(frames, path, durations, loop=0):
    q = palette_quantize(frames)
    q[0].save(path, save_all=True, append_images=q[1:], duration=durations, loop=loop,
              optimize=True, disposal=1)
    print(f"{path.name}: {len(frames)} frames, {path.stat().st_size/1024:.0f} KB")


def splash_gif(pw_browser):
    ctx = pw_browser.new_context(viewport={"width": 960, "height": 540}, device_scale_factor=1)
    page = ctx.new_page()
    page.goto(uri(ROOT / "splash" / "index.html") + "?capture=1")
    page.wait_for_load_state("load")
    page.evaluate("__start().then(r => { window.__r = r; })")
    page.wait_for_function("window.__r")
    total = page.evaluate("window.__r.duration")
    n = int(total / 1000 * FPS)
    frames, shots = [], []
    for i in range(n + 1):
        t = min(total - 1, i * 1000 / FPS)
        page.evaluate("t => window.__r.animations.forEach(a => { a.currentTime = t })", t)
        png = TMP / f"s{i:03d}.png"
        page.screenshot(path=str(png))
        frames.append(Image.open(png).convert("RGB"))
    durs = [round(1000 / FPS)] * len(frames)
    durs[-1] = 2200  # hold on the finished UI before looping
    save_gif(frames, GIF / "splash-intro.gif", durs)
    # contact sheet for eyeballing
    keys = [frames[round(n * f)] for f in (0.05, 0.15, 0.3, 0.45, 0.6, 0.75, 0.88, 1.0)]
    sheet = Image.new("RGB", (480 * 4, 270 * 2))
    for i, k in enumerate(keys):
        sheet.paste(k.resize((480, 270)), ((i % 4) * 480, (i // 4) * 270))
    sheet.save(TMP / "splash-sheet.png")
    ctx.close()
    return TMP / "splash-sheet.png"


def loop_pages(browser):
    """Seamless needle sway: 36 frames = 2.4s. Full mark >=64px, small mark below."""
    N, AMP = 36, 10
    results = {}
    for size, svg_name in ((48, "mark-small.svg"), (96, "mark.svg"), (180, "mark.svg"), (512, "mark.svg")):
        svg = (LOGO / svg_name).read_text(encoding="utf-8")
        svg = re.sub(r'<svg [^>]*>', f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180" width="{size}" height="{size}">', svg, 1)
        svg = svg.replace('<g transform="rotate(30 90 90)"', '<g id="nd" transform="rotate(30 90 90)"', 1)
        html = TMP / f"loop{size}.html"
        html.write_text(f"<!doctype html><meta charset=utf-8><style>html,body{{margin:0;background:transparent}}svg{{display:block}}</style>{svg}")
        ctx = browser.new_context(viewport={"width": size, "height": size}, device_scale_factor=1)
        page = ctx.new_page()
        page.goto(uri(html))
        solid, alpha = [], []
        for i in range(N):
            ang = 30 + AMP * math.sin(2 * math.pi * i / N)
            page.evaluate("a => document.getElementById('nd').setAttribute('transform', `rotate(${a} 90 90)`)", ang)
            png = TMP / f"l{size}_{i:02d}.png"
            page.screenshot(path=str(png), omit_background=True)
            rgba = Image.open(png).convert("RGBA")
            alpha.append(rgba)
            bg = Image.new("RGBA", rgba.size, PAPER)
            bg.alpha_composite(rgba)
            solid.append(bg.convert("RGB"))
        ctx.close()
        save_gif(solid, GIF / f"mark-loop-{size}.gif", [round(1000 / FPS)] * N)
        results[size] = alpha
    results[512][0].save(GIF / "mark-loop-512-transparent.webp", save_all=True,
                         append_images=results[512][1:], duration=round(1000 / FPS), loop=0,
                         lossless=True, quality=90)
    p = GIF / "mark-loop-512-transparent.webp"
    print(f"{p.name}: {p.stat().st_size/1024:.0f} KB")


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(device_scale_factor=1)
        page = ctx.new_page()
        static_assets(page)
        ctx.close()
        sheet = splash_gif(browser)
        loop_pages(browser)
        browser.close()
    print("contact sheet:", sheet)


if __name__ == "__main__":
    main()
