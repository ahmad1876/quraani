"""One-off check that everything is connected. Prints what is missing.

Run from GitHub: Actions > "Quraani daily" > Run workflow > mode: check
It also creates the gh-pages branch so GitHub Pages can be switched on.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

ok = True


def report(name: str, good: bool, detail: str = "") -> None:
    global ok
    ok &= good
    print(f"[{'OK' if good else 'MISSING'}] {name}" + (f" - {detail}" if detail else ""), flush=True)


def main() -> int:
    # Buffer
    try:
        import buffer_api
        ch = buffer_api.pick_channels()
        report("Buffer API key", True)
        for p in ("tiktok", "instagram"):
            report(f"Buffer channel: {p}", p in ch, ch[p]["name"] if p in ch else "connect it in Buffer")
        if "youtube" in ch:
            print(f"[OK] Buffer channel: youtube (optional) - {ch['youtube']['name']}")
    except Exception as e:
        report("Buffer API key", False, str(e)[:200])

    # Footage library
    try:
        lib = json.loads((ROOT / "catalog" / "footage.json").read_text(encoding="utf-8"))
        good = [c for c in lib if c.get("ok", True)]
        report("Footage library", len(good) >= 30, f"{len(good)} clips")
        req = urllib.request.Request(good[0]["url"], headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-1023"})
        with urllib.request.urlopen(req, timeout=60) as r:
            report("Footage download", r.status in (200, 206), good[0]["url"].split("/")[2])
    except Exception as e:
        report("Footage library", False, str(e)[:200])

    # Hosting
    import storage
    if storage.backend() == "r2":
        report("Hosting", True, "Cloudflare R2")
    else:
        try:
            created = storage.ensure_pages_branch()
            base = storage.pages_base()
            if created:
                print("Created the gh-pages branch.")
            try:
                storage.wait_live(base + "/v/check.txt" if created else base + "/", timeout=240)
                report("GitHub Pages", True, base)
            except Exception:
                report("GitHub Pages", False, "turn it on: repo Settings > Pages > Source: Deploy from a branch > "
                                              "gh-pages / (root) > Save, then run check again")
        except Exception as e:
            report("GitHub Pages", False, str(e)[:300])

    print("\nAll set." if ok else "\nFix the MISSING items above, then run again.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
