"""Learn from what performs: post stats from Buffer -> gentle weights for the next picks.

Each daily run (scripts/daily.py) calls update():
  1. fetch the stats of posts sent in the last few weeks from Buffer into state/metrics.json
  2. score every video against a typical post of the same age on the same platform
     (0 = typical, +0.7 = about twice the views, -0.7 = about half)
  3. turn the scores into weights per passage theme, reciter and footage mood, pulled
     toward neutral while there are only a few posts, and kept between 0.6 and 1.8 so
     nothing is ever dropped: state/stats_weights.json (read by planner.py and footage.py)
  4. write a short readable summary to state/stats.md

Everything here is best effort: if Buffer is unreachable the old weights stay in place.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from collections import defaultdict
from statistics import median

from common import CATALOG, STATE, load_json, save_json

METRICS = STATE / "metrics.json"
WEIGHTS = STATE / "stats_weights.json"
REPORT = STATE / "stats.md"

WINDOW_DAYS = 21      # fetch posts sent in this window
REFRESH_HOURS = 20    # re-fetch a post's stats at most this often
FINAL_DAYS = 14       # after this the numbers barely move: stop refreshing
MIN_AGE_H = 44        # posts younger than this don't count toward the weights yet
PRIOR = 3.0           # shrinkage: a factor needs a few posts before it moves far from 1.0
STRENGTH = 0.8        # weight = exp(STRENGTH * score)
W_MIN, W_MAX = 0.6, 1.8
PLATFORM_NAMES = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}

POSTS_QUERY = """query($first: Int, $after: String, $input: PostsInput!) {
  posts(first: $first, after: $after, input: $input) {
    edges { node { id dueAt sentAt status externalLink metricsUpdatedAt metrics { type value } } }
    pageInfo { hasNextPage endCursor }
  }
}"""


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _when(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# ------------------------------------------------------------------ fetch --
def fetch_sent(chans: dict, since: dt.datetime, gql=None) -> dict[str, dict]:
    """{post_id: node} for posts sent on our Buffer channels since `since`."""
    if gql is None:
        import buffer_api
        gql = buffer_api.gql
    by_org: dict[str, list[str]] = defaultdict(list)
    for ch in chans.values():
        if ch.get("organization"):
            by_org[ch["organization"]].append(ch["id"])
    out: dict[str, dict] = {}
    for org, ids in by_org.items():
        after = None
        for _ in range(20):  # 20 pages x 50 posts is far more than a few weeks of posts
            data = gql(POSTS_QUERY, {"first": 50, "after": after, "input": {
                "organizationId": org,
                "filter": {"channelIds": ids, "status": ["sent"],
                           "dueAt": {"start": since.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                     "end": _now().strftime("%Y-%m-%dT%H:%M:%S.000Z")}},
                "sort": [{"field": "dueAt", "direction": "desc"}]}})["posts"]
            for e in data.get("edges") or []:
                n = e["node"]
                out[n["id"]] = n
            page = data.get("pageInfo") or {}
            if not page.get("hasNextPage") or not page.get("endCursor"):
                break
            after = page["endCursor"]
    return out


def _passage_theme() -> dict[str, str]:
    ps = load_json(CATALOG / "passages.json", []) or []
    return {p["id"]: p.get("theme", "") for p in ps}


def collect(history: list[dict], chans: dict, gql=None) -> dict:
    """Refresh state/metrics.json from Buffer. Returns the metrics store."""
    store = load_json(METRICS, {}) or {}
    now = _now()
    themes = _passage_theme()
    wanted = {}
    for h in history:
        for platform, p in (h.get("posts") or {}).items():
            due = _when(p.get("due"))
            if not p.get("id") or not due or due > now or due < now - dt.timedelta(days=WINDOW_DAYS):
                continue
            old = store.get(p["id"], {})
            fetched = _when(old.get("fetched"))
            if old.get("missing", 0) >= 3:  # deleted in Buffer (e.g. the paused TikTok posts): stop asking
                continue
            if fetched and (fetched > now - dt.timedelta(hours=REFRESH_HOURS)
                            or fetched > due + dt.timedelta(days=FINAL_DAYS)):
                continue
            wanted[p["id"]] = (platform, due, h)
    if wanted:
        nodes = fetch_sent(chans, min(w[1] for w in wanted.values()) - dt.timedelta(hours=1), gql=gql)
        for pid, (platform, due, h) in wanted.items():
            n = nodes.get(pid)
            if not n:
                old = store.get(pid, {})
                store[pid] = {**old, "platform": platform, "due": due.isoformat(), "date": h.get("date"),
                              "slot": h.get("slot"), "fetched": now.isoformat(timespec="seconds"),
                              "missing": old.get("missing", 0) + 1}
                continue
            m = {x["type"]: x["value"] for x in (n.get("metrics") or []) if x.get("type") is not None}
            store[pid] = {
                "platform": platform, "due": due.isoformat(), "date": h.get("date"), "slot": h.get("slot"),
                "passage": h.get("passage"), "theme": themes.get(h.get("passage"), ""), "reciter": h.get("reciter"),
                "mood": h.get("footage_theme", ""), "duration": h.get("duration"), "link": n.get("externalLink"),
                "fetched": now.isoformat(timespec="seconds"), "m": m,
            }
    # forget posts that dropped out of the window long ago
    cutoff = now - dt.timedelta(days=60)
    store = {k: v for k, v in store.items() if (_when(v.get("due")) or now) > cutoff}
    save_json(METRICS, store)
    return store


# ------------------------------------------------------------------ score --
def _views(m: dict) -> float | None:
    for k in ("views", "impressions", "reach", "viewers"):
        if m.get(k) is not None:
            return float(m[k])
    return None


def _likes(m: dict) -> float:
    return float(m.get("likes") if m.get("likes") is not None else m.get("reactions") or 0)


def _residuals(rows: list[tuple[float, float]]) -> list[float]:
    """ln(value+1) minus what a typical post of that age gets (fit on ln(age) once there are enough posts)."""
    ys = [math.log(v + 1) for v, _ in rows]
    if len(rows) >= 8:
        xs = [math.log(max(a, 1.0)) for _, a in rows]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx > 1e-9 else 0.0
        b = min(max(b, 0.0), 1.0)
        a = my - b * mx
        return [y - (a + b * x) for x, y in zip(xs, ys)]
    mid = median(ys)
    return [y - mid for y in ys]


def scores(store: dict, now: dt.datetime | None = None) -> dict[str, dict]:
    """Per post: {'score', 'views', 'likes', 'platform', ...}; only posts old enough to judge."""
    now = now or _now()
    by_platform: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for pid, r in store.items():
        due = _when(r.get("due"))
        if not due or due > now - dt.timedelta(hours=MIN_AGE_H) or _views(r.get("m") or {}) is None:
            continue
        by_platform[r["platform"]].append((pid, r))
    out = {}
    for platform, items in by_platform.items():
        if len(items) < 4:  # too few to say what "typical" is
            continue
        ages = [max((now - _when(r["due"])).total_seconds() / 86400, 1.0) for _, r in items]
        v_res = _residuals([(_views(r["m"]), a) for (_, r), a in zip(items, ages)])
        l_res = _residuals([(_likes(r["m"]), a) for (_, r), a in zip(items, ages)])
        for (pid, r), vr, lr, age in zip(items, v_res, l_res, ages):
            out[pid] = {**r, "score": 0.75 * vr + 0.25 * lr, "views": _views(r["m"]), "likes": _likes(r["m"]),
                        "age_days": round(age, 1)}
    return out


def factor_table(post_scores: dict) -> dict[str, dict[str, dict]]:
    """{factor: {value: {'n', 'mean', 'shrunk', 'weight'}}} with one score per video (platforms averaged)."""
    per_video: dict[tuple, list[float]] = defaultdict(list)
    info = {}
    for r in post_scores.values():
        key = (r.get("date"), r.get("slot"), r.get("passage"))
        per_video[key].append(r["score"])
        info[key] = r
    acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for key, ss in per_video.items():
        s = sum(ss) / len(ss)
        r = info[key]
        for factor in ("theme", "reciter", "mood"):
            if r.get(factor):
                acc[factor][r[factor]].append(s)
    table: dict[str, dict[str, dict]] = {}
    for factor, vals in acc.items():
        table[factor] = {}
        for v, ss in vals.items():
            shrunk = sum(ss) / (len(ss) + PRIOR)
            table[factor][v] = {"n": len(ss), "mean": round(sum(ss) / len(ss), 3), "shrunk": round(shrunk, 3),
                                "weight": round(min(max(math.exp(STRENGTH * shrunk), W_MIN), W_MAX), 3)}
    return table


# ---------------------------------------------------------------- weights --
def weights() -> dict[str, dict[str, float]]:
    """{'theme': {...}, 'reciter': {...}, 'mood': {...}} -> weight (missing = 1.0). Never raises."""
    try:
        data = load_json(WEIGHTS, {}) or {}
        return {k: {v: min(max(float(x), W_MIN), W_MAX) for v, x in (data.get(k) or {}).items()}
                for k in ("theme", "reciter", "mood")}
    except Exception:
        return {"theme": {}, "reciter": {}, "mood": {}}


def weight(kind: str, value: str, table: dict | None = None) -> float:
    table = table if table is not None else weights()
    return float(table.get(kind, {}).get(value, 1.0))


# ----------------------------------------------------------------- report --
def _fmt(x: float) -> str:
    return f"{x:+.2f}"


def report(post_scores: dict, table: dict, store: dict, now: dt.datetime | None = None) -> str:
    now = now or _now()
    from zoneinfo import ZoneInfo
    sa = ZoneInfo("Africa/Johannesburg")
    lines = [f"# Quraani stats (updated {now.astimezone(sa):%Y-%m-%d %H:%M} SA time)", "",
             "Score = views (and a little likes) compared with a typical post of the same age on the same platform. "
             "0 is typical, +0.7 is about twice the views, -0.7 about half. Posts count once they are 2 days old.", ""]
    lines += ["## Platforms", "", "| Platform | Posts with stats | Counted | Median views | Total views | Total likes |",
              "|---|---|---|---|---|---|"]
    for platform in ("youtube", "instagram", "tiktok"):
        rows = [r for r in store.values() if r["platform"] == platform and _views(r.get("m") or {}) is not None]
        if not rows:
            continue
        vs = [_views(r["m"]) for r in rows]
        counted = sum(1 for r in post_scores.values() if r["platform"] == platform)
        lines.append(f"| {PLATFORM_NAMES[platform]} | {len(rows)} | {counted} | {median(vs):.0f} | {sum(vs):.0f} | "
                     f"{sum(_likes(r['m']) for r in rows):.0f} |")
    names = {"theme": "Passage themes", "reciter": "Reciters", "mood": "Footage moods"}
    for factor in ("theme", "reciter", "mood"):
        vals = table.get(factor) or {}
        if not vals:
            continue
        ranked = sorted(vals.items(), key=lambda kv: -kv[1]["shrunk"])
        lines += ["", f"## {names[factor]}", "", "| | Videos | Score | Weight now |", "|---|---|---|---|"]
        for v, d in ranked:
            lines.append(f"| {v} | {d['n']} | {_fmt(d['mean'])} | {d['weight']:.2f} |")
    # posting times, per platform (report only)
    times: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in post_scores.values():
        due = _when(r["due"])
        times[(r["platform"], f"{due.astimezone(sa):%H:%M}")].append(r["score"])
    if times:
        lines += ["", "## Posting times (SA time)", "", "| Platform | Time | Posts | Score |", "|---|---|---|---|"]
        for (platform, t), ss in sorted(times.items()):
            lines.append(f"| {PLATFORM_NAMES[platform]} | {t} | {len(ss)} | {_fmt(sum(ss) / len(ss))} |")
    top = sorted(post_scores.values(), key=lambda r: -r["score"])
    if top:
        lines += ["", "## Best and weakest posts", "", "| Platform | Date | Passage | Reciter | Mood | Views | Score |",
                  "|---|---|---|---|---|---|---|"]
        pick = top[:5] + ([r for r in top[-3:] if r not in top[:5]] if len(top) > 5 else [])
        for r in pick:
            lines.append(f"| {PLATFORM_NAMES[r['platform']]} | {r.get('date')} | {r.get('passage')} | {r.get('reciter')} | "
                         f"{r.get('mood')} | {r['views']:.0f} | {_fmt(r['score'])} |")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------- main --
def update(history: list[dict], chans: dict, gql=None) -> str:
    """Fetch, score, save weights and report. Returns a one-line summary for the log."""
    store = collect(history, chans, gql=gql)
    post_scores = scores(store)
    table = factor_table(post_scores)
    save_json(WEIGHTS, {f: {v: d["weight"] for v, d in vals.items()} for f, vals in table.items()})
    REPORT.write_text(report(post_scores, table, store), encoding="utf-8")
    n_stats = sum(1 for r in store.values() if _views(r.get("m") or {}) is not None)
    return f"stats: {n_stats} posts with numbers, {len(post_scores)} old enough to count"
