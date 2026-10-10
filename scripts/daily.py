"""Daily job: keep the next few days of TikTok / Instagram / YouTube Shorts posts scheduled.

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
import quran  # noqa: E402
import recitation  # noqa: E402
import stats  # noqa: E402
import storage  # noqa: E402
import trends  # noqa: E402
from common import STATE, config, load_json, save_json  # noqa: E402

PLATFORMS = ("tiktok", "instagram", "youtube")


def platform_on(cfg: dict, platform: str, slot_id: str, day: dt.date) -> bool:
    """config "platform_plan" can pause a platform or limit it to some slots from a date on."""
    rules = sorted(cfg.get("platform_plan", {}).get(platform, []), key=lambda r: r["from"])
    current = None
    for r in rules:
        if r["from"] <= day.isoformat():
            current = r
    return current is None or slot_id in current.get("slots", [])


def active_slot(cfg: dict, slot: dict, day: dt.date) -> dict:
    """The slot with only the platforms that post on that day. A weekly slot with "replaces" (the Friday
    long video) takes the place of that slot's posts on its days, so the posts per day stay the same."""
    out = {k: v for k, v in slot.items() if k not in PLATFORMS or platform_on(cfg, k, slot["id"], day)}
    for other in cfg.get("slots", []):
        if other.get("replaces") == slot["id"] and day.weekday() in other.get("weekdays", []):
            for p in PLATFORMS:
                if other.get(p) and platform_on(cfg, p, other["id"], day):
                    out.pop(p, None)
    return out


def open_slots(cfg: dict, schedule: dict, now: dt.datetime, days_ahead: int) -> list[tuple[dt.date, dict]]:
    tz = now.tzinfo
    out = []
    for d in range(0, days_ahead + 1):
        day = (now + dt.timedelta(days=d)).date()
        for slot in cfg["slots"]:
            if schedule.get(day.isoformat(), {}).get(slot["id"]):
                continue
            if "weekdays" in slot and day.weekday() not in slot["weekdays"]:  # e.g. the weekly long video
                continue
            slot = active_slot(cfg, slot, day)
            times = [slot[p] for p in PLATFORMS if slot.get(p)]
            if not times:  # nothing posts from this slot on that day
                continue
            latest = max(dt.datetime.combine(day, dt.time.fromisoformat(t), tz) for t in times)
            if latest < now + dt.timedelta(minutes=50):  # every post time of this slot is too close or past
                continue
            out.append((day, slot))
    return out



def due_utc(day: dt.date, when: str, tz) -> dt.datetime:
    return dt.datetime.combine(day, dt.time.fromisoformat(when), tz).astimezone(dt.timezone.utc)


def post_one(platform: str, ch: dict, due: dt.datetime, meta: dict, rec: dict, url: str, cfg: dict) -> dict:
    """Schedule one video on one Buffer channel. Returns the history record for that post."""
    text = captions.build(meta, platform, rec["tag"], credit=cfg.get("credit_footage", True),
                          handle=cfg.get("handle", ""))
    post = buffer_api.schedule_video(
        ch, text, url, due.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        youtube_title=captions.youtube_title(meta) if platform == "youtube" else "")
    return {"id": post["id"], "due": post.get("dueAt") or due.isoformat()}


def caption_meta(entry: dict) -> dict:
    """Rebuild the caption fields for a video that is already online (from its history entry)."""
    ch, rng = entry["ref"].split(":")
    a, b = (rng.split("-") + [rng])[:2]
    ch, a, b = int(ch), int(a), int(b)
    passage = planner.by_id().get(entry["passage"], {})
    rec = recitation.reciters()[entry["reciter"]]
    return {
        "passage": entry["passage"], "ref": entry["ref"], "surah": ch,
        "surah_en": quran.chapter(ch)["name_en"], "reciter": entry["reciter"], "reciter_en": rec["name_en"],
        "hook": passage.get("hook", ""), "theme": passage.get("theme", ""),
        "translation": make.join_translation([quran.verse(f"{ch}:{k}")["translation"] for k in range(a, b + 1)]),
        "sources": sorted({c.split(":")[0] for c in entry.get("clips", []) if c.split(":")[0] in ("pexels", "mixkit")}),
    }


def backfill(cfg: dict, history: list[dict], chans: dict, tz) -> int:
    """Add posts that are missing for videos already online: a channel connected later
    (e.g. YouTube) or a post that failed on an earlier run. Only for times still ahead."""
    slots = {s["id"]: s for s in cfg["slots"]}
    soon = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=20)
    added = 0
    for entry in history:
        slot = slots.get(entry.get("slot"))
        if not slot or not entry.get("url") or "posts" not in entry:
            continue
        day = dt.date.fromisoformat(entry["date"])
        slot = active_slot(cfg, slot, day)
        todo = [p for p in PLATFORMS if slot.get(p) and chans.get(p) and p not in entry["posts"]
                and due_utc(day, slot[p], tz) > soon]
        if not todo:
            continue
        if not storage.is_live(entry["url"]):
            print(f"   {entry['ref']}: video no longer online, cannot add {todo}", flush=True)
            continue
        meta = caption_meta(entry)
        rec = recitation.reciters()[entry["reciter"]]
        for platform in todo:
            try:
                entry["posts"][platform] = post_one(platform, chans[platform], due_utc(day, slot[platform], tz),
                                                    meta, rec, entry["url"], cfg)
                added += 1
                print(f"   {entry['ref']}: added {platform} for {day} {slot[platform]}", flush=True)
            except buffer_api.QueueFull as e:
                print(f"   {entry['ref']}: {platform} queue is full, will retry next run ({e})", flush=True)
            except Exception as e:
                print(f"   {entry['ref']}: {platform} failed: {e}", flush=True)
    return added

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
        added = backfill(cfg, history, chans, tz)
        if added:
            save_json(STATE / "history.json", history[-2000:])
            print(f"Added {added} missing post(s) for videos already online.", flush=True)
        if cfg.get("learn_from_stats", True):
            try:  # best effort: never let stats stop the posting
                print(stats.update(history, chans), "(see state/stats.md)", flush=True)
            except Exception as e:
                if "insights:read" in str(e):
                    print("stats: skipped - the Buffer API key needs the insights:read permission. Make a new key in "
                          "Buffer (Settings > API) with account:read, posts:read, posts:write and insights:read, and "
                          "paste it over the BUFFER_API_KEY secret.", flush=True)
                else:
                    print(f"stats: skipped this run ({type(e).__name__}: {str(e)[:300]})", flush=True)
        if cfg.get("learn_from_trends", True):
            try:  # best effort, like the stats step
                print(trends.update(), flush=True)
            except Exception as e:
                print(f"trends: skipped this run ({type(e).__name__}: {str(e)[:300]})", flush=True)

    slots = open_slots(cfg, schedule, now, days_ahead)
    if args.max:
        slots = slots[: args.max]
    if not slots:
        print("Nothing to do: the next", days_ahead, "days are already scheduled.")
        return 0
    rng = random.Random()
    # the weekly long slot picks from the long list; the rest from the daily shorts
    longs = [x for x in slots if x[1].get("long")]
    slots = [x for x in slots if not x[1].get("long")] + longs
    picks = planner.choose(len(slots) - len(longs), history, rng)
    if longs:
        long_picks = planner.choose(len(longs), history, rng, long=True)
        if len(picks) < len(slots) - len(longs):  # keep each pick next to its own kind of slot
            slots = slots[:len(picks)] + longs
        picks += long_picks
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
                                   seed=rng.randint(0, 10 ** 6), preset=cfg.get("x264_preset", "medium"),
                                   english=cfg.get("english", True), hook_title=cfg.get("hook_title", True))
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
            due = due_utc(day, when, tz)
            if due < dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=15):
                print(f"   {meta['ref']}: {platform} time {day} {when} has passed, skipped", flush=True)
                continue
            try:
                entry["posts"][platform] = post_one(platform, ch, due, meta, rec, url, cfg)
                print(f"   {meta['ref']}: scheduled on {platform} for {day} {when} ({ch['name']})", flush=True)
            except buffer_api.QueueFull as e:
                print(f"   {meta['ref']}: {platform} queue is full, will retry next run ({e})", flush=True)
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
