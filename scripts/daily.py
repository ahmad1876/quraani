"""Daily job: keep the next few days of TikTok / Instagram (and optional YouTube) posts scheduled.

Run by GitHub Actions once a day. Safe to re-run: filled slots are skipped.
  python scripts/daily.py             # render, publish to GitHub Pages, schedule on Buffer
  python scripts/daily.py --dry-run   # render only
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import sys
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import buffer_api  # noqa: E402
import captions  # noqa: E402
import make  # noqa: E402
import planner  # noqa: E402
import recitation  # noqa: E402
import storage  # noqa: E402
from common import STATE, config, load_json, save_json  # noqa: E402

PLATFORMS = ("tiktok", "instagram", "youtube")


def open_slots(cfg: dict, schedule: dict, now: dt.datetime, days_ahead: int) -> list[tuple[dt.date, dict]]:
    tz = now.tzinfo
    out = []
    for d in range(0, days_ahead + 1):
        day = (now + dt.timedelta(days=d)).date()
        for slot in cfg["slots"]:
            if schedule.get(day.isoformat(), {}).get(slot["id"]):
                continue
            times = [slot[p] for p in PLATFORMS if slot.get(p)]
            earliest = min(dt.datetime.combine(day, dt.time.fromisoformat(t), tz) for t in times)
            if earliest < now + dt.timedelta(minutes=50):
                continue
            out.append((day, slot))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=int)
    ap.add_argument("--max", type=int, default=0, help="render at most this many videos this run")
    ap.add_argument("--out", default=str(ROOT / "out"))
    args = ap.parse_args()

    cfg = config()
    tz = ZoneInfo(cfg.get("timezone", "Africa/Johannesburg"))
    now = dt.datetime.now(tz)
    history = load_json(STATE / "history.json", []) or []
    schedule = load_json(STATE / "schedule.json", {}) or {}
    days_ahead = args.days if args.days is not None else int(cfg.get("days_ahead", 2))

    chans = {}
    if not args.dry_run:
        chans = buffer_api.pick_channels()
        print("Buffer channels:", {k: v["name"] for k, v in chans.items()})
        missing = [p for p in ("tiktok", "instagram") if p not in chans]
        if missing:
            print(f"WARNING: connect {missing} in Buffer - those posts are skipped")
        if not chans:
            print("No Buffer channels connected - nothing to schedule.")
            return 1

    slots = open_slots(cfg, schedule, now, days_ahead)
    if args.max:
        slots = slots[: args.max]
    if not slots:
        print("Nothing to do: the next", days_ahead, "days are already scheduled.")
        return 0
    rng = random.Random()
    picks = planner.choose(len(slots), history, rng)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    errors = 0

    # 1) render everything first
    made = []
    for (day, slot), (passage, rk) in zip(slots, picks):
        rec = recitation.reciters()[rk]
        name = f"{day.isoformat()}_{slot['id']}_{passage['id'].replace(':', '-')}_{rk}.mp4"
        out = out_dir / name
        print(f"\n== {day} slot {slot['id']}: {passage['id']} / {rec['name_en']}", flush=True)
        try:
            meta = make.make_video(passage, rk, out, history=history, handle=cfg.get("handle", ""),
                                   seed=rng.randint(0, 10 ** 6), preset=cfg.get("x264_preset", "medium"))
        except Exception:
            traceback.print_exc()
            errors += 1
            continue
        print(f"   rendered {meta['ref']} ({meta['duration']}s, {Path(out).stat().st_size / 1e6:.1f} MB)", flush=True)
        made.append((day, slot, rec, meta, out))
        # rotation memory so the next video in this run avoids the same clips/themes
        history.append({"passage": meta["passage"], "reciter": rk, "clips": meta["clips"],
                        "footage_theme": meta.get("footage_theme", ""), "_tmp": True})
    history = [h for h in history if not h.get("_tmp")]
    if not made:
        return 1
    if args.dry_run:
        return 1 if errors else 0

    # 2) one publish for the whole batch (keeps files that still have posts waiting)
    keep = storage.pending_names(history, dt.datetime.now(dt.timezone.utc))
    urls = storage.publish([str(m[4]) for m in made], keep)
    print("\nPublished:", *urls.values(), sep="\n  ", flush=True)

    # 3) schedule on Buffer
    for day, slot, rec, meta, out in made:
        url = urls[out.name]
        entry = {"date": day.isoformat(), "slot": slot["id"], "made": now.isoformat(timespec="seconds"),
                 **{k: meta[k] for k in ("passage", "ref", "reciter", "duration", "clips")},
                 "footage_theme": meta.get("footage_theme", ""), "url": url, "posts": {}}
        for platform in PLATFORMS:
            when, ch = slot.get(platform), chans.get(platform)
            if not when or not ch:
                continue
            due = dt.datetime.combine(day, dt.time.fromisoformat(when), tz).astimezone(dt.timezone.utc)
            text = captions.build(meta, platform, rec["tag"], credit=cfg.get("credit_footage", True))
            try:
                post = buffer_api.schedule_video(
                    ch, text, url, due.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    youtube_title=captions.youtube_title(meta) if platform == "youtube" else "")
                entry["posts"][platform] = {"id": post["id"], "due": post.get("dueAt") or due.isoformat()}
                print(f"   {meta['ref']}: scheduled on {platform} for {day} {when} ({ch['name']})", flush=True)
            except Exception as e:
                print(f"   {meta['ref']}: {platform} failed: {e}", flush=True)
                errors += 1
        history.append(entry)
        schedule.setdefault(day.isoformat(), {})[slot["id"]] = f"{meta['ref']}|{meta['reciter']}"
        save_json(STATE / "history.json", history[-2000:])
        cutoff = (now - dt.timedelta(days=30)).date().isoformat()
        save_json(STATE / "schedule.json", {d: v for d, v in schedule.items() if d >= cutoff})
        out.unlink(missing_ok=True)

    if len(picks) < len(slots):
        print(f"WARNING: only planned {len(picks)} of {len(slots)} slots")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
