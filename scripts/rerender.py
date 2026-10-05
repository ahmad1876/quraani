"""Re-render videos that are already scheduled but not yet posted, in the current style.

Same passage, reciter, ayahs and footage; same file name and URL, so the posts already
waiting in Buffer pick up the new version when they go out (Buffer fetches media at
publish time). Nothing in Buffer is changed.

  python scripts/rerender.py            # all pending videos
  python scripts/rerender.py --dry-run  # list them only
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import sys
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import make  # noqa: E402
import planner  # noqa: E402
import storage  # noqa: E402
from common import STATE, config, load_json  # noqa: E402

_spec = importlib.util.spec_from_file_location("daily", ROOT / "scripts" / "daily.py")
daily = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(daily)


def pending(cfg: dict, history: list[dict], tz) -> list[dict]:
    """History entries with a post still ahead on a platform that is active for that day."""
    soon = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=25)
    slots = {s["id"]: s for s in cfg["slots"]}
    out = []
    for e in history:
        if not e.get("url") or not e.get("clips") or e.get("slot") not in slots:
            continue
        day = dt.date.fromisoformat(e["date"])
        live = daily.active_slot(cfg, slots[e["slot"]], day)
        for platform, post in e.get("posts", {}).items():
            due = dt.datetime.fromisoformat(post["due"].replace("Z", "+00:00"))
            if platform in live and due > soon:
                out.append(e)
                break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "out" / "rerender"))
    args = ap.parse_args()
    cfg = config()
    tz = ZoneInfo(cfg.get("timezone", "Africa/Johannesburg"))
    history = load_json(STATE / "history.json", []) or []
    todo = pending(cfg, history, tz)
    print(f"{len(todo)} scheduled video(s) to re-render", flush=True)
    for e in todo:
        print(f"  {e['date']} {e['slot']} {e['ref']} {e['reciter']} -> {sorted(e['posts'])}", flush=True)
    if args.dry_run or not todo:
        return 0
    by_id = {p["id"]: p for p in planner.passages()}
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    made, errors = [], 0
    for e in todo:
        name = e["url"].rsplit("/", 1)[-1]
        try:
            meta = make.make_video(by_id[e["passage"]], e["reciter"], out_dir / name, handle=cfg.get("handle", ""),
                                   seed=7, preset=cfg.get("x264_preset", "medium"),
                                   english=cfg.get("english", True), clip_ids=e["clips"])
            if meta["ref"] != e["ref"]:
                print(f"  {name}: ayahs changed ({e['ref']} -> {meta['ref']}), keeping the old video", flush=True)
                (out_dir / name).unlink(missing_ok=True)
                continue
            made.append(str(out_dir / name))
            print(f"  re-rendered {name} ({meta['duration']}s)", flush=True)
        except Exception:
            traceback.print_exc()
            errors += 1
    if made:
        keep = storage.pending_names(history, dt.datetime.now(dt.timezone.utc))
        urls = storage.publish(made, keep)
        print("Updated:", *urls.values(), sep="\n  ", flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
