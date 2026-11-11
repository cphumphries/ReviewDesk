"""Make the timeline pictures for the user guide and the README, from the reference review's plan.

    python tools/make_timeline_image.py

writes docs/images/timeline.svg and timeline.png (landscape), and timeline-portrait.svg and timeline-portrait.png,
the pictures at twice their size. The SVGs come from timeline.svg_generic(); the PNGs are drawn from them by
Chromium (Playwright), with ReviewDesk's own fonts, so they look exactly as they do in the app. Run it again
whenever the steps (app/reviewdesk/workflow.py) or the picture (app/reviewdesk/timeline.py) change."""
from __future__ import annotations

import base64
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from reviewdesk import timeline  # noqa: E402

FONTS = os.path.join(ROOT, "app", "reviewdesk", "static", "fonts")
FACES = [("Plex", 400, "ibm-plex-sans-latin-400-normal.woff2"), ("Plex", 600, "ibm-plex-sans-latin-600-normal.woff2"),
         ("Serif4", 400, "source-serif-4-latin-400-normal.woff2"), ("Serif4", 600, "source-serif-4-latin-600-normal.woff2")]
OUT = os.path.join(ROOT, "docs", "images")
PICTURES = (("landscape", "timeline"), ("portrait", "timeline-portrait"))


def page(svg: str) -> str:
    """The picture on an otherwise empty page, with the app's fonts built in (nothing is fetched)."""
    faces = "".join(
        f'@font-face{{font-family:{fam};font-weight:{w};src:url(data:font/woff2;base64,'
        f'{base64.b64encode(open(os.path.join(FONTS, f), "rb").read()).decode()}) format("woff2")}}'
        for fam, w, f in FACES)
    return f'<!doctype html><html><head><meta charset="utf-8"><style>{faces}html,body{{margin:0;background:#fff}}svg{{display:block}}</style></head><body>{svg}</body></html>'


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("This needs Playwright with Chromium: pip install playwright, then playwright install chromium.")
        return 1
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for variant, name in PICTURES:
            svg = timeline.svg_generic(variant)
            with open(os.path.join(OUT, name + ".svg"), "w", encoding="utf-8", newline="\n") as f:
                f.write(svg)
            w = float(svg.split('width="', 1)[1].split('"', 1)[0])
            h = float(svg.split('height="', 1)[1].split('"', 1)[0])
            pg = browser.new_page(viewport={"width": int(w) + 1, "height": int(h) + 1}, device_scale_factor=2)
            pg.set_content(page(svg))
            loaded = pg.evaluate("Promise.all([...document.fonts].map((f) => f.load())).then(() => document.fonts.ready)"
                                 ".then(() => [...document.fonts].filter((f) => f.status === 'loaded').length)")
            if loaded < len(FACES):
                print(f"Only {loaded} of the {len(FACES)} fonts loaded for {name}; the picture may use other fonts.")
            pg.locator("svg").screenshot(path=os.path.join(OUT, name + ".png"))
            pg.close()
            print(f"Saved docs/images/{name}.png ({int(w) * 2} x {int(h) * 2}) and {name}.svg")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
