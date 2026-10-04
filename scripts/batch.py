"""Render a batch of ready-to-post videos into a folder, each with its captions in a .txt file.

  python scripts/batch.py 6 out/batch
  python scripts/batch.py 0 out/batch 39:53-54=yasser-aldosari 55:1-13=mishary-alafasy
Uses the same rotation as the daily job but does not upload or schedule anything.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import captions  # noqa: E402
import make  # noqa: E402
import planner  # noqa: E402
import recitation  # noqa: E402
from common import STATE, config, load_json  # noqa: E402


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    out_dir = Path(sys.argv[2] if len(sys.argv) > 2 else ROOT / "out" / "batch")
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = config()
    history = load_json(STATE / "history.json", []) or []
    rng = random.Random()
    chosen = [a.split("=") for a in sys.argv[3:] if "=" in a]
    if chosen:
        by_id = {p["id"]: p for p in planner.passages()}
        picks = [(by_id[pid], rk) for pid, rk in chosen]
    else:
        picks = planner.choose(n, history, rng)
    for i, (p, rk) in enumerate(picks, 1):
        rec = recitation.reciters()[rk]
        stem = f"{i:02d}_{p['id'].replace(':', '-')}_{rk}"
        mp4 = out_dir / f"{stem}.mp4"
        print(f"[{i}/{len(picks)}] {p['id']} - {rec['name_en']}", flush=True)
        meta = make.make_video(p, rk, mp4, history=history, handle=cfg.get("handle", ""),
                               seed=rng.randint(0, 10 ** 6), preset=cfg.get("x264_preset", "medium"))
        history.append({"passage": meta["passage"], "reciter": rk, "clips": meta["clips"],
                        "footage_theme": meta.get("footage_theme", "")})
        txt = [
            f"{meta['ref']} | {meta['reciter_en']} | {meta['duration']}s", "",
            "=== TikTok caption ===", captions.build(meta, "tiktok", rec["tag"]), "",
            "=== Instagram caption ===", captions.build(meta, "instagram", rec["tag"]), "",
        ]
        (out_dir / f"{stem}.txt").write_text("\n".join(txt), encoding="utf-8")
        print(f"   -> {mp4.name} ({meta['duration']}s, {meta['ref']})", flush=True)


if __name__ == "__main__":
    main()
