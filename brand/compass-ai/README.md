# Compass AI — brand kit (Inkline)

A compass drawn in a single ink line, with a clay-red needle. The splash draws it on, cycles a few greetings
beside the wordmark, then dissolves into the app while the logo glides into the header.

## What's in the box

| Folder | Contents | Use for |
|---|---|---|
| `logo/` | `mark.svg`, `mark-reversed.svg`, `mark-mono.svg`, `mark-small.svg`, `mark-small-reversed.svg`, `mark-animated.svg`, `lockup.svg`, `lockup-reversed.svg`, `word.svg`, `word-reversed.svg`, `favicon.svg` | Source of truth. Wordmark is outlined, so no font is needed. |
| `png/` | `mark-{64…1024}.png`, `mark-small-{16,32,48}.png`, `mark-reversed-512.png`, `lockup-{600,1200,2400}.png`, `lockup-reversed-1200.png` | Docs, slides, README, social. Transparent backgrounds. |
| `favicon/` | `favicon.svg`, `favicon.ico` (16/32/48), `favicon-{16,32,48}.png`, `apple-touch-icon.png`, `icon-192/512.png`, `icon-maskable-512.png`, `site.webmanifest`, `head-snippet.html` | Browser tab, home-screen / PWA icons |
| `gif/` | `splash-intro.gif` (960×540, 5s), `mark-loop-{48,96,180,512}.gif`, `mark-loop-512-transparent.webp` | Places that can't run code: README, Slack, email, docs. Chat avatar / "thinking" indicator. |
| `splash/` | `compass-splash.js`, `compass-splash.css`, `index.html` (live demo) | The real in-app intro. Prefer this over the GIF inside the app: it's crisp at any size, random greetings, 8 KB of dependency-free JS (unminified). |
| `fonts/` | Caveat (greetings, subset), Instrument Serif (source for the outlined wordmark), OFL licences | Keep the licence files with the fonts. |
| `tokens.css` | Colour + font variables | App CSS |
| `tools/` | `build_logo.py`, `render_assets.py` | Regenerate everything after a change |

## Colour

| Token | Hex | Use |
|---|---|---|
| Ink | `#1F2937` | Mark, wordmark, primary buttons |
| Paper | `#F7F4EE` | Splash and icon background |
| Paper 2 | `#FBF9F5` | App surface |
| Clay | `#C2562B` | Needle and graphics only (3:1 on Paper) |
| Clay (text) | `#B4532A` | Greeting text (4.5:1 on Paper) |
| Clay on dark | `#E88A63` | Needle on dark backgrounds |

## Using the logo

- **Mark vs. small mark.** Use the full mark at **64px and up**. Below 64px use `mark-small` (heavier ring, solid needle, no ticks); the thin inner ring and ticks disappear at that size.
- **Lockup** (mark + wordmark): minimum width **120px**. Use the mark alone where space is tight (avatar, favicon).
- **Clear space:** keep a quarter of the mark's height free on every side.
- **Dark backgrounds:** use the `-reversed` files. Never put the ink version on a dark surface.
- **One colour:** `mark-mono.svg` follows `currentColor` (stamps, emboss, print).
- **Don't:** recolour the needle, rotate the mark, stretch it, add shadows, or change the resting needle angle (30° clockwise from north).
- **Accessibility:** the SVGs carry a `<title>` and `aria-label`. The splash overlay is `aria-hidden`, skips on click/Esc/Enter, and shortens to ~1s under `prefers-reduced-motion`.

## Add the splash to the Flask app

1. Copy this folder to `static/brand/compass-ai/` (Flask serves `static/` automatically).
2. In `templates/index.html`:

```html
<!-- in <head> -->
<link rel="stylesheet" href="{{ url_for('static', filename='brand/compass-ai/splash/compass-splash.css') }}">

<!-- somewhere in the header: the slot the logo lands in -->
<div id="brand-slot" style="height:44px;width:133px">
  <img src="{{ url_for('static', filename='brand/compass-ai/logo/lockup.svg') }}" alt="Compass AI" style="height:100%">
</div>

<!-- before </body> -->
<script src="{{ url_for('static', filename='brand/compass-ai/splash/compass-splash.js') }}"></script>
<script>
  CompassSplash.play({
    assetBase: "{{ url_for('static', filename='brand/compass-ai/') }}",
    target: "#brand-slot",
    once: "compass-splash-seen"   // optional: only once per browser tab
  });
</script>
```

Options: `greetings` (your own list), `count` (how many to cycle, default 3), `random` (default true),
`background` (default Paper), `onDone`. Open `splash/index.html` in a browser to see it running.

Note: the app currently has a dark theme. The splash dissolves into whatever is behind it, but the
ink lockup in the header slot needs a light surface; on dark UI use `lockup-reversed.svg` and pass
`background: "#0f172a"` to the splash (the mark in the splash is the ink version, so for a dark
splash swap `.cs-dr` stroke and the wordmark to the reversed colours in `compass-splash.css`).

## Regenerating assets

```
pip install fonttools uharfbuzz playwright pillow
python tools/build_logo.py      # logo/*.svg from one source
python tools/render_assets.py   # png/, favicon/, gif/  (uses Microsoft Edge; no browser download)
```

GIFs use a shared 128-colour palette and 15fps to stay small; the in-app splash has no such limits.

## Licences

Instrument Serif and Caveat are SIL Open Font License 1.1 (`fonts/OFL-*.txt`). The logo files are yours.
