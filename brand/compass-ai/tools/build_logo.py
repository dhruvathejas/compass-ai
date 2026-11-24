"""Builds every vector file in logo/ from one source of truth.

Run with a Python that has: fonttools, uharfbuzz
    python tools/build_logo.py
The wordmark is converted to outlines, so the SVGs need no installed font.
"""
from pathlib import Path
import uharfbuzz as hb
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "logo"
LOGO.mkdir(exist_ok=True)
FONT = ROOT / "fonts" / "InstrumentSerif-Regular.ttf"

INK, PAPER, CLAY = "#1F2937", "#F7F4EE", "#C2562B"
CLAY_ON_DARK = "#E88A63"
REST = 30  # resting needle angle, degrees clockwise from north
WORD = "Compass AI"
WORD_SIZE = 84  # px in a 180px-tall lockup
GAP = 36


def mark_body(ink, clay, center_fill="none", extra_class=""):
    """Full-detail mark (use at 64px and above)."""
    return f'''<g fill="none" stroke="{ink}" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="90" cy="90" r="76" stroke-width="3.5"/>
    <circle cx="90" cy="90" r="64" stroke-width="1.8"/>
    <path d="M90 8v10M90 162v10M8 90h10M162 90h10" stroke-width="2.5"/>
    <g transform="rotate({REST} 90 90)"{extra_class}>
      <path d="M90 34L103 90H77z" fill="{clay}" stroke="none"/>
      <path d="M90 34L103 90L90 146L77 90Z" stroke-width="3.5"/>
    </g>
    <circle cx="90" cy="90" r="4" stroke-width="2.5" fill="{center_fill}"/>
  </g>'''


def small_body(ink, clay, face):
    """Simplified mark for 16-48px (heavier ring, solid needle, no ticks)."""
    return f'''<circle cx="90" cy="90" r="80" fill="{face}" stroke="{ink}" stroke-width="11"/>
  <g transform="rotate({REST} 90 90)">
    <path d="M90 28L112 90H68z" fill="{clay}"/>
    <path d="M90 152L112 90H68z" fill="{ink}"/>
  </g>'''


def svg(viewbox, body, title="Compass AI", w=None, h=None, extra=""):
    vb = viewbox.split()
    w = w or vb[2]
    h = h or vb[3]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" width="{w}" height="{h}" '
            f'role="img" aria-label="{title}"{extra}>\n  <title>{title}</title>\n  {body}\n</svg>\n')


def word_outline(text, size):
    """Shape `text` with HarfBuzz (kerning on) and return (path_d, width, cap_height_px)."""
    blob = hb.Blob.from_file_path(str(FONT))
    face = hb.Face(blob)
    font = hb.Font(face)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, {"kern": True, "liga": True})
    tt = TTFont(str(FONT))
    gs = tt.getGlyphSet()
    order = tt.getGlyphOrder()
    upem = tt["head"].unitsPerEm
    s = size / upem
    cap = getattr(tt["OS/2"], "sCapHeight", 0.7 * upem) * s
    pen = SVGPathPen(gs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
    x = 0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        name = order[info.codepoint]
        # flip y (font y-up -> svg y-down), scale, translate
        tp = TransformPen(pen, (s, 0, 0, -s, (x + pos.x_offset) * s, -pos.y_offset * s))
        gs[name].draw(tp)
        x += pos.x_advance
    return pen.getCommands(), x * s, cap


def lockup(ink, clay, center_fill):
    d, width, cap = word_outline(WORD, WORD_SIZE)
    baseline = 90 + cap / 2
    total = 180 + GAP + width
    body = (mark_body(ink, clay, center_fill) +
            f'\n  <path transform="translate({180 + GAP} {baseline:.2f})" fill="{ink}" d="{d}"/>')
    return svg(f"0 0 {total:.0f} 180", body, "Compass AI"), total


def animated():
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180" width="180" height="180" role="img" aria-label="Compass AI">
  <title>Compass AI</title>
  <style>
    .dr{{fill:none;stroke:{INK};stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1;stroke-dashoffset:1;animation:draw 1.2s ease-in-out forwards}}
    .d2{{animation-delay:.25s}} .d3{{animation-delay:.5s}}
    @keyframes draw{{to{{stroke-dashoffset:0}}}}
    .fill{{fill:{CLAY};opacity:0;animation:fade .4s 1.1s forwards}}
    @keyframes fade{{to{{opacity:1}}}}
    .nd{{transform-origin:90px 90px;transform:rotate({REST}deg);animation:settle 2.4s 1.4s ease-in-out forwards,sway 4.8s 3.8s ease-in-out infinite}}
    @keyframes settle{{0%{{transform:rotate({REST}deg)}}25%{{transform:rotate({REST+14}deg)}}55%{{transform:rotate({REST-14}deg)}}80%{{transform:rotate({REST+6}deg)}}100%{{transform:rotate({REST}deg)}}}}
    @keyframes sway{{0%,100%{{transform:rotate({REST}deg)}}50%{{transform:rotate({REST+7}deg)}}}}
    @media (prefers-reduced-motion:reduce){{.dr,.fill,.nd{{animation:none;stroke-dashoffset:0;opacity:1}}}}
  </style>
  <circle class="dr" cx="90" cy="90" r="76" pathLength="1" stroke-width="3.5"/>
  <circle class="dr d2" cx="90" cy="90" r="64" pathLength="1" stroke-width="1.8"/>
  <path class="dr d2" d="M90 8v10M90 162v10M8 90h10M162 90h10" pathLength="1" stroke-width="2.5"/>
  <g class="nd">
    <path class="fill" d="M90 34L103 90H77z"/>
    <path class="dr d3" d="M90 34L103 90L90 146L77 90Z" pathLength="1" stroke-width="3.5"/>
  </g>
  <circle class="dr d3" cx="90" cy="90" r="4" pathLength="1" stroke-width="2.5"/>
</svg>
'''


def write(name, text):
    (LOGO / name).write_text(text, encoding="utf-8")
    print("wrote logo/" + name)


def main():
    write("mark.svg", svg("0 0 180 180", mark_body(INK, CLAY)))
    write("mark-reversed.svg", svg("0 0 180 180", mark_body(PAPER, CLAY_ON_DARK)))
    write("mark-mono.svg", svg("0 0 180 180", mark_body("currentColor", "currentColor")))
    write("mark-small.svg", svg("0 0 180 180", small_body(INK, CLAY, PAPER)))
    write("mark-small-reversed.svg", svg("0 0 180 180", small_body(PAPER, CLAY_ON_DARK, INK)))
    write("favicon.svg", svg(
        "0 0 180 180", small_body(INK, CLAY, PAPER), extra=""))
    # the favicon folder is what head-snippet.html points at, so keep a copy there
    fav = ROOT / "favicon"
    fav.mkdir(exist_ok=True)
    (fav / "favicon.svg").write_text((LOGO / "favicon.svg").read_text(encoding="utf-8"), encoding="utf-8")
    write("mark-animated.svg", animated())
    lk, _ = lockup(INK, CLAY, "none")
    write("lockup.svg", lk)
    lkr, _ = lockup(PAPER, CLAY_ON_DARK, "none")
    write("lockup-reversed.svg", lkr)
    d, w, cap = word_outline(WORD, WORD_SIZE)
    baseline = 90 + cap / 2
    for name, color in (("word.svg", INK), ("word-reversed.svg", PAPER)):
        write(name, svg(f"0 0 {w:.0f} 180", f'<path transform="translate(0 {baseline:.2f})" fill="{color}" d="{d}"/>', "Compass AI"))
    print(f"wordmark width {w:.1f}px, cap height {cap:.1f}px at size {WORD_SIZE}")


if __name__ == "__main__":
    main()
