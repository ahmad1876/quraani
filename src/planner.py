"""Choose what to post next: popular passages x popular reciters, without repeats.

Besides the gentle weights from our own stats and from YouTube trends:
  - most videos go to the voices people watch most (config "focus_reciters": the top of the trends
    leaderboard get first try on about 70% of videos)
  - a few favourite surahs come back at least once a week (config "weekly_surahs")
  - once a week at most, a clear winner is remade with another top voice (config "remake")
  - the opening line alternates between the verse's own words and a line that speaks to the viewer,
    leaning toward whichever does better (config "hook_test")
"""
from __future__ import annotations

import datetime as dt
import json
import random

import make
import recitation
import stats
import trends
from common import CATALOG, STATE, config, load_json

PRIORITY_WEIGHT = {1: 3.0, 2: 1.6, 3: 0.8}
TIER_WEIGHT = {1: 3.0, 2: 1.7, 3: 0.8}


def passages() -> list[dict]:
    return json.loads((CATALOG / "passages.json").read_text(encoding="utf-8"))


def long_passages() -> list[dict]:
    """Passages for the weekly long video (up to 3 minutes), each marked "long": true."""
    path = CATALOG / "long_passages.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def by_id() -> dict[str, dict]:
    """Every passage, short and long, by id (long ids are kept distinct by a "long" record)."""
    out = {p["id"]: p for p in passages()}
    out.update({"long:" + p["id"]: p for p in long_passages()})
    return out


def passage_key(p: dict) -> str:
    """How a passage is named in history.json: long ones carry a 'long:' prefix."""
    return ("long:" if p.get("long") else "") + p["id"]


def _weighted_order(items, weight, rng):
    # Efraimidis-Spirakis: random order biased by weight
    keyed = [(rng.random() ** (1.0 / max(weight(i), 1e-6)), i) for i in items]
    return [i for _, i in sorted(keyed, key=lambda x: -x[0])]


def focus_keys(cfg: dict | None = None) -> set[str]:
    """Reciter keys of the voices people watch most right now: the top of the trends leaderboard."""
    cfg = cfg if cfg is not None else config()
    top = int((cfg.get("focus_reciters") or {}).get("top", 8))
    board = (load_json(STATE / "trends.json", {}) or {}).get("leaderboard") or {}
    ranked = sorted(board.values(), key=lambda b: -b.get("top10", 0))[:top]
    keys = {k for b in ranked for k in b.get("keys", [])}
    recs = recitation.reciters()
    if not keys:  # no leaderboard yet: the first tier
        keys = {k for k, r in recs.items() if r.get("tier") == 1}
    return keys & set(recs)


def due_weekly(history: list[dict], cfg: dict | None = None, today: dt.date | None = None) -> list[int]:
    """Favourite surahs (config "weekly_surahs") not posted as a short in the last 7 days, longest gap first."""
    cfg = cfg if cfg is not None else config()
    today = today or dt.date.today()
    last: dict[int, str] = {}
    for h in history:
        pid = str(h.get("passage", ""))
        if not pid or pid.startswith("long:") or not h.get("date"):
            continue
        su = int(pid.split(":")[0])
        last[su] = max(last.get(su, ""), h["date"])
    cutoff = (today - dt.timedelta(days=6)).isoformat()
    due = [su for su in cfg.get("weekly_surahs") or [] if last.get(su, "") < cutoff]
    return sorted(due, key=lambda su: last.get(su, ""))


def hook_style(rng: random.Random, cfg: dict | None = None) -> str:
    """"you" (a line that speaks to the viewer) or "verse" (the verse's words): half and half at first,
    then leaning toward whichever does better, never below 1 in 4."""
    cfg = cfg if cfg is not None else config()
    if not cfg.get("hook_test", True):
        return "verse"
    w = stats.weights().get("hook", {})
    wy, wv = float(w.get("you", 1.0)), float(w.get("verse", 1.0))
    p = min(max(wy / (wy + wv), 0.25), 0.75)
    return "you" if rng.random() < p else "verse"


def remake_pick(history: list[dict], rng: random.Random, cfg: dict | None = None,
                today: dt.date | None = None) -> tuple[dict, str, str] | None:
    """At most once a week: (passage, reciter, "date_slot" of the original) for a clear winner, remade
    with a different voice (the top voices first) and new footage. None when there is nothing to do."""
    cfg = cfg if cfg is not None else config()
    rc = cfg.get("remake") or {}
    if not rc or rc.get("off"):
        return None
    today = today or dt.date.today()
    every, min_age = int(rc.get("every_days", 7)), int(rc.get("min_age_days", 7))
    min_score = float(rc.get("min_score", 0.7))
    since = (today - dt.timedelta(days=every - 1)).isoformat()
    if any(h.get("remake_of") and h.get("date", "") >= since for h in history):
        return None
    done = {h["remake_of"] for h in history if h.get("remake_of")}
    oldest = (today - dt.timedelta(days=min_age)).isoformat()
    used_pairs = {(h["passage"], h["reciter"]) for h in history}
    recs, focus, catalog = recitation.reciters(), focus_keys(cfg), by_id()
    tier = lambda k: TIER_WEIGHT.get(recs[k]["tier"], 1.0)
    for v in stats.video_scores():
        if v["score"] < min_score:
            break
        key = f"{v['date']}_{v['slot']}"
        p = catalog.get(v["passage"])
        if key in done or v["date"] > oldest or not p or p.get("long") or v["reciter"] not in recs:
            continue
        order = (_weighted_order(sorted(focus), tier, rng) +
                 _weighted_order(sorted(set(recs) - focus), tier, rng))
        for rk in order:
            if recs[rk]["tag"] == recs[v["reciter"]]["tag"] or (v["passage"], rk) in used_pairs:
                continue
            try:
                _, span = make.plan_span(p, rk)
            except Exception:
                continue
            if span:
                return p, rk, key
    return None


def choose(n: int, history: list[dict], rng: random.Random | None = None,
           passage_gap: int = 80, reciter_gap: int = 3, long: bool = False, cfg: dict | None = None,
           exclude: set[str] | frozenset = frozenset(), today: dt.date | None = None) -> list[tuple[dict, str]]:
    """Return n (passage, reciter_key) pairs that fit the length limit (long: from the weekly long list).
    exclude: passage keys already taken (e.g. by a remake in the same run)."""
    rng = rng or random.Random()
    cfg = cfg if cfg is not None else config()
    recs = recitation.reciters()
    learned = stats.weights()  # from post stats (state/stats_weights.json); empty = neutral
    trending = trends.weights()  # from popular Quran Shorts on YouTube (state/trends.json); empty = neutral
    recent_p = {h["passage"] for h in history[-passage_gap:]}
    recent_r = [h["reciter"] for h in history[-reciter_gap:]]
    used_pairs = {(h["passage"], h["reciter"]) for h in history}
    picks: list[tuple[dict, str]] = []
    pool = long_passages() if long else passages()
    if long:  # long videos come once a week: look back over the last dozen of them
        recent_p = set(list(dict.fromkeys(h["passage"] for h in history if h["passage"].startswith("long:")))[-12:])
    pool = [p for p in pool if passage_key(p) not in exclude]
    ps = [p for p in pool if passage_key(p) not in recent_p] or pool
    p_weight = lambda p: (PRIORITY_WEIGHT.get(p["priority"], 1.0) * stats.weight("theme", p.get("theme", ""), learned)
                          * trends.weight("surah", p["surah"], trending))
    r_weight = lambda k: (TIER_WEIGHT.get(recs[k]["tier"], 1.0) * stats.weight("reciter", k, learned)
                          * trends.weight("reciter", k, trending))
    order = _weighted_order(ps, p_weight, rng)
    if not long:  # one favourite surah that is due this week goes first, even if used recently
        for su in due_weekly(history, cfg, today)[:1]:
            fav = [p for p in pool if p["surah"] == su]
            if fav:
                first = _weighted_order(fav, p_weight, rng)[0]
                order = [first] + [p for p in order if p is not first]
    focus = focus_keys(cfg)
    share = float((cfg.get("focus_reciters") or {}).get("share", 0.7))
    for p in order:
        if len(picks) >= n:
            break
        if any(passage_key(p) == passage_key(q) for q, _ in picks):
            continue
        taken_r = recent_r + [r for _, r in picks]
        ranked = _weighted_order(list(recs), r_weight, rng)
        top_first = rng.random() < share  # the top voices get first try on most videos
        ranked = sorted(ranked, key=lambda k: (k in focus) != top_first)
        for rk in ranked:
            if rk in taken_r or (passage_key(p), rk) in used_pairs:
                continue
            # one Abdul Basit style per batch is plenty
            if recs[rk]["tag"] in {recs[r]["tag"] for _, r in picks}:
                continue
            try:
                _, span = make.plan_span(p, rk)
            except Exception:
                continue
            if span:
                picks.append((p, rk))
                break
    return picks
