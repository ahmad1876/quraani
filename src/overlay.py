"""Render the text overlays (Arabic phrase + English line) as transparent PNGs with headless Chromium.

Chromium's HarfBuzz shaping gives correct Uthmani script with every mark,
which simpler text renderers often get wrong.
"""
from __future__ import annotations

import html
from pathlib import Path

from common import FONTS

W, H = 1080, 1920
TEXT_CENTER_Y = 900      # a little above centre, clear of the app UI at the bottom
TEXT_MAX_H = 660         # Arabic + English together; kept compact so the scenery leads
TEXT_LEFT, TEXT_WIDTH = 90, 900
AR_MAX, AR_MIN = 74, 42  # Arabic size range (px)
EN_MAX, EN_MIN = 40, 30  # English size range (px)

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
@font-face{{font-family:'AQ';src:url('{fonts}/AmiriQuran-Regular.ttf')}}
@font-face{{font-family:'AM';src:url('{fonts}/Amiri-Regular.ttf')}}
@font-face{{font-family:'AMB';src:url('{fonts}/Amiri-Bold.ttf')}}
html,body{{margin:0;padding:0;background:transparent}}
body{{width:{W}px;height:{H}px;position:relative;overflow:hidden}}
#ayah{{position:absolute;left:{L}px;width:{TW}px;top:0;text-align:center;padding:60px 34px;box-sizing:border-box;
  background:radial-gradient(closest-side,rgba(0,0,0,{B1}) 0%,rgba(0,0,0,{B2}) 50%,rgba(0,0,0,0) 100%)}}
#ar{{direction:rtl;font-family:'AQ';color:#fff;line-height:1.85;word-spacing:.05em;
  text-shadow:0 0 30px rgba(0,0,0,.65),0 0 12px rgba(0,0,0,.5),0 2px 5px rgba(0,0,0,.85)}}
#en{{direction:ltr;font-family:'AM';color:rgba(255,255,255,.9);line-height:1.32;margin:14px auto 0;max-width:820px;
  letter-spacing:.2px;text-shadow:0 0 14px rgba(0,0,0,.85),0 0 4px rgba(0,0,0,.7),0 1px 3px rgba(0,0,0,.95)}}
#en:empty{{display:none}}
#hdr{{position:absolute;left:190px;width:700px;top:280px;text-align:center;direction:rtl;padding:16px 0 20px}}
#hdr .s{{font-family:'AMB';font-size:54px;line-height:1.35;color:#F2E2BC;
  text-shadow:0 0 20px rgba(0,0,0,.7),0 0 6px rgba(0,0,0,.5),0 2px 6px rgba(0,0,0,.7)}}
#hdr .orn{{display:flex;align-items:center;justify-content:center;gap:14px;margin:4px 0 3px}}
#hdr .orn i{{display:block;height:2px;width:96px;background:linear-gradient(90deg,transparent,#E7CF9A,transparent);opacity:.9}}
#hdr .orn b{{display:block;width:9px;height:9px;transform:rotate(45deg);background:#E7CF9A;box-shadow:0 0 6px rgba(0,0,0,.5)}}
#hdr .r{{font-family:'AM';font-size:40px;line-height:1.35;color:#fff;
  text-shadow:0 0 18px rgba(0,0,0,.85),0 0 6px rgba(0,0,0,.7),0 2px 5px rgba(0,0,0,.85)}}
#hdr .re{{direction:ltr;font-family:'AM';font-size:29px;line-height:1.3;color:rgba(255,255,255,.92);letter-spacing:.3px;
  text-shadow:0 0 16px rgba(0,0,0,.85),0 0 5px rgba(0,0,0,.7),0 1px 4px rgba(0,0,0,.9)}}
#hdr .re:empty{{display:none}}
#handle{{position:absolute;left:0;width:{W}px;top:1440px;text-align:center;font:600 31px 'DejaVu Sans',sans-serif;
  color:rgba(255,255,255,.84);letter-spacing:.6px;text-shadow:0 0 10px rgba(0,0,0,.55),0 1px 4px rgba(0,0,0,.8);padding:8px 0}}
#hook{{position:absolute;left:100px;width:880px;top:0;text-align:center;direction:ltr;padding:18px 0 22px;display:none}}
#hook .t{{font-family:'AMB';color:#F2E2BC;line-height:1.2;text-wrap:balance;
  text-shadow:0 0 22px rgba(0,0,0,.75),0 0 8px rgba(0,0,0,.55),0 2px 6px rgba(0,0,0,.8)}}
#hook .orn{{display:flex;align-items:center;justify-content:center;gap:14px;margin:12px 0 0}}
#hook .orn i{{display:block;height:2px;width:120px;background:linear-gradient(90deg,transparent,#E7CF9A,transparent);opacity:.9}}
#hook .orn b{{display:block;width:9px;height:9px;transform:rotate(45deg);background:#E7CF9A;box-shadow:0 0 6px rgba(0,0,0,.5)}}
</style></head><body>
<div id="hdr"><div class="s">{surah}</div><div class="orn"><i></i><b></b><i></i></div><div class="r">{reciter}</div><div class="re">{reciter_en}</div></div>
<div id="hook"><div class="t"></div><div class="orn"><i></i><b></b><i></i></div></div>
<div id="ayah"><div id="ar"></div><div id="en"></div></div><div id="handle">{handle}</div>
</body></html>"""

# Hook title: largest size (px) at which the line fits in two lines (three for long lines),
# centred on the header so the crossfade from hook to header stays in one place.
HOOK_JS = """([text, maxPx, minPx]) => {
  const box = document.getElementById('hook'), t = box.querySelector('.t'), hdr = document.getElementById('hdr');
  box.style.display = 'block'; t.textContent = text;
  const lines = () => Math.round(t.offsetHeight / parseFloat(getComputedStyle(t).lineHeight));
  const maxLines = text.length <= 44 ? 2 : 3;
  let s = maxPx;
  for (;;) {
    t.style.fontSize = s + 'px';
    if (lines() <= maxLines || s <= minPx) break;
    s -= 2;
  }
  const hc = hdr.offsetTop + hdr.offsetHeight / 2;
  box.style.top = Math.max(150, Math.round(hc - box.offsetHeight / 2)) + 'px';
  return [s, lines(), box.offsetTop, box.offsetHeight];
}"""


def hook_title(text: str) -> str:
    """The passage's hook line as an on-screen title: no closing full stop, single spaces."""
    t = " ".join((text or "").split())
    return t[:-1] if t.endswith(".") and not t.endswith("...") else t

# Largest sizes (Arabic first, English follows at about half) that fit the box; English keeps to 4 lines.
FIT_JS = """([ar, en, arMax, arMin, enMax, enMin]) => {
  const box = document.getElementById('ayah'), a = document.getElementById('ar'), e = document.getElementById('en');
  a.textContent = ar; e.textContent = en || '';
  const enLines = () => e.textContent ? Math.round(e.offsetHeight / parseFloat(getComputedStyle(e).lineHeight)) : 0;
  let s = arMax;
  for (;;) {
    a.style.fontSize = s + 'px';
    let es = Math.max(enMin, Math.min(enMax, Math.round(s * 0.56)));
    e.style.fontSize = es + 'px';
    while (es > enMin && enLines() > 4) { es -= 1; e.style.fontSize = es + 'px'; }
    if (box.scrollHeight <= %d || s <= arMin) return [s, es];
    s -= 2;
  }
}""" % TEXT_MAX_H

PLACE_JS = """([ar, en, s, es]) => {
  const box = document.getElementById('ayah'), a = document.getElementById('ar'), e = document.getElementById('en');
  a.textContent = ar; e.textContent = en || '';
  a.style.fontSize = s + 'px'; e.style.fontSize = es + 'px';
  box.style.top = Math.round(%d - box.offsetHeight / 2) + 'px';
  return box.offsetHeight;
}""" % TEXT_CENTER_Y


def render(units: list[dict], surah_ar: str, reciter_ar: str, out_dir: Path, handle: str = "",
           english: bool = True, backdrop: float = 0.34, hook: str = "", reciter_en: str = "") -> dict:
    """Writes header.png (+handle.png, +hook.png) and unit_XX.png. Returns positions for compositing.

    backdrop: darkness of the soft shade behind the text (higher on bright, busy footage).
    hook: short title shown in place of the header for the first seconds (empty = no title).
    reciter_en: the reciter's name in English under the Arabic one (people search for reciters by name)."""
    from playwright.sync_api import sync_playwright

    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    page_html = PAGE.format(fonts=FONTS.as_uri(), W=W, H=H, L=TEXT_LEFT, TW=TEXT_WIDTH,
                            surah=html.escape(surah_ar), reciter=html.escape(reciter_ar), reciter_en=html.escape(reciter_en),
                            handle=html.escape(handle), B1=f"{backdrop:.2f}", B2=f"{backdrop * 0.65:.2f}")
    page_file = out_dir / "overlay.html"
    page_file.write_text(page_html, encoding="utf-8")
    result = {"units": [], "header": None, "handle": None, "hook": None}
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
        title = hook_title(hook)
        if title:
            page.evaluate(HOOK_JS, [title, 66, 46])  # measures against the header, so before hiding it
            page.evaluate("document.getElementById('hdr').style.visibility='hidden';"
                          "document.getElementById('handle').style.visibility='hidden'")
            hk = page.locator("#hook")
            bb = hk.bounding_box()
            hk.screenshot(path=str(out_dir / "hook.png"), omit_background=True)
            result["hook"] = {"png": str(out_dir / "hook.png"), "x": int(bb["x"]), "y": int(bb["y"])}
            page.evaluate("document.getElementById('hook').style.display='none'")
        page.evaluate("document.getElementById('hdr').style.display='none';document.getElementById('handle').style.display='none'")
        texts = [(u["text"], u.get("en", "") if english else "") for u in units]
        # each phrase fits on its own, but sizes stay within a narrow band per video
        fits = [page.evaluate(FIT_JS, [ar, en, AR_MAX, AR_MIN, EN_MAX, EN_MIN]) for ar, en in texts]
        floor_ar = min(f[0] for f in fits)
        floor_en = min((f[1] for f, t in zip(fits, texts) if t[1]), default=EN_MIN)
        for i, u in enumerate(units):
            ar, en = texts[i]
            s = min(fits[i][0], floor_ar + 10)
            es = min(fits[i][1], floor_en + 3)
            page.evaluate(PLACE_JS, [ar, en, s, es])
            el = page.locator("#ayah")
            bb = el.bounding_box()
            png = out_dir / f"unit_{i:02d}.png"
            el.screenshot(path=str(png), omit_background=True)
            result["units"].append({"png": str(png), "x": int(bb["x"]), "y": int(bb["y"]),
                                    "start": u["start"], "end": u["end"], "size": s})
        browser.close()
    return result
