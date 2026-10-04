"""Render Arabic text overlays as transparent PNGs with headless Chromium.

Chromium's HarfBuzz shaping gives correct Uthmani script with every mark,
which simpler text renderers often get wrong.
"""
from __future__ import annotations

import html
from pathlib import Path

from common import FONTS

W, H = 1080, 1920
TEXT_CENTER_Y = 900      # a little above centre, clear of the app UI at the bottom
TEXT_MAX_H = 640
TEXT_LEFT, TEXT_WIDTH = 80, 920

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
@font-face{{font-family:'AQ';src:url('{fonts}/AmiriQuran-Regular.ttf')}}
@font-face{{font-family:'AM';src:url('{fonts}/Amiri-Regular.ttf')}}
@font-face{{font-family:'AMB';src:url('{fonts}/Amiri-Bold.ttf')}}
html,body{{margin:0;padding:0;background:transparent}}
body{{width:{W}px;height:{H}px;position:relative;overflow:hidden}}
#ayah{{position:absolute;left:{L}px;width:{TW}px;top:0;direction:rtl;text-align:center;
  font-family:'AQ';color:#fff;line-height:1.95;padding:34px 18px;box-sizing:border-box;
  text-shadow:0 0 34px rgba(0,0,0,.7),0 0 14px rgba(0,0,0,.55),0 2px 5px rgba(0,0,0,.85);word-spacing:.05em}}
#hdr{{position:absolute;left:140px;width:800px;top:270px;text-align:center;direction:rtl;padding:18px 0 22px}}
#hdr .s{{font-family:'AMB';font-size:66px;line-height:1.35;color:#F2E2BC;
  text-shadow:0 0 18px rgba(0,0,0,.55),0 2px 6px rgba(0,0,0,.6)}}
#hdr .orn{{display:flex;align-items:center;justify-content:center;gap:16px;margin:6px 0 4px}}
#hdr .orn i{{display:block;height:2px;width:120px;background:linear-gradient(90deg,transparent,#E7CF9A,transparent);opacity:.9}}
#hdr .orn b{{display:block;width:11px;height:11px;transform:rotate(45deg);background:#E7CF9A;box-shadow:0 0 6px rgba(0,0,0,.5)}}
#hdr .r{{font-family:'AM';font-size:42px;line-height:1.4;color:rgba(255,255,255,.9);
  text-shadow:0 0 14px rgba(0,0,0,.6),0 2px 5px rgba(0,0,0,.6)}}
#handle{{position:absolute;left:0;width:{W}px;top:1440px;text-align:center;font:500 30px 'DejaVu Sans',sans-serif;
  color:rgba(255,255,255,.62);letter-spacing:.5px;text-shadow:0 1px 4px rgba(0,0,0,.6);padding:8px 0}}
</style></head><body>
<div id="hdr"><div class="s">{surah}</div><div class="orn"><i></i><b></b><i></i></div><div class="r">{reciter}</div></div>
<div id="ayah"></div><div id="handle">{handle}</div>
</body></html>"""

FIT_JS = """([text, maxSize, minSize]) => {
  const el = document.getElementById('ayah');
  el.textContent = text;
  let s = maxSize; el.style.fontSize = s + 'px';
  while (s > minSize && el.scrollHeight > %d) { s -= 2; el.style.fontSize = s + 'px'; }
  return s;
}""" % TEXT_MAX_H

PLACE_JS = """([size]) => {
  const el = document.getElementById('ayah');
  el.style.fontSize = size + 'px';
  el.style.top = Math.round(%d - el.offsetHeight / 2) + 'px';
  return el.offsetHeight;
}""" % TEXT_CENTER_Y


def render(units: list[dict], surah_ar: str, reciter_ar: str, out_dir: Path, handle: str = "") -> dict:
    """Writes header.png (+handle.png) and unit_XX.png. Returns positions for compositing."""
    from playwright.sync_api import sync_playwright

    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    page_html = PAGE.format(fonts=FONTS.as_uri(), W=W, H=H, L=TEXT_LEFT, TW=TEXT_WIDTH,
                            surah=html.escape(surah_ar), reciter=html.escape(reciter_ar),
                            handle=html.escape(handle))
    page_file = out_dir / "overlay.html"
    page_file.write_text(page_html, encoding="utf-8")
    result = {"units": [], "header": None, "handle": None}
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--font-render-hinting=none"])
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        page.goto(page_file.as_uri())
        page.evaluate("document.fonts.ready.then(() => true)")
        page.wait_for_timeout(250)
        # header
        hdr = page.locator("#hdr")
        bb = hdr.bounding_box()
        hdr.screenshot(path=str(out_dir / "header.png"), omit_background=True)
        result["header"] = {"png": str(out_dir / "header.png"), "x": int(bb["x"]), "y": int(bb["y"])}
        if handle:
            hl = page.locator("#handle")
            bb = hl.bounding_box()
            hl.screenshot(path=str(out_dir / "handle.png"), omit_background=True)
            result["handle"] = {"png": str(out_dir / "handle.png"), "x": int(bb["x"]), "y": int(bb["y"])}
        page.evaluate("document.getElementById('hdr').style.display='none';document.getElementById('handle').style.display='none'")
        # pick sizes: each unit fits on its own, but sizes stay within a narrow band per video
        sizes = [page.evaluate(FIT_JS, [u["text"], 98, 50]) for u in units]
        floor = min(sizes)
        for i, u in enumerate(units):
            size = min(sizes[i], floor + 16)
            page.evaluate(FIT_JS, [u["text"], 98, 50])
            page.evaluate(PLACE_JS, [size])
            el = page.locator("#ayah")
            bb = el.bounding_box()
            png = out_dir / f"unit_{i:02d}.png"
            el.screenshot(path=str(png), omit_background=True)
            result["units"].append({"png": str(png), "x": int(bb["x"]), "y": int(bb["y"]),
                                    "start": u["start"], "end": u["end"], "size": size})
        browser.close()
    return result
