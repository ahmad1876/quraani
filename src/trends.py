"""What is popular right now: the most viewed Quran Shorts on YouTube -> a gentle nudge for the next picks.

Once a week (scripts/daily.py calls update()):
  1. search YouTube for the most viewed Quran recitation Shorts of the last 30 days (a few searches,
     500 units, plus 100 per reciter: about 3,300 of the free 10,000 daily units)
  2. read each video's title (and, when the title names none, a description naming exactly one) for the
     surah and the reciter (English or Arabic names)
  3. search YouTube once per reciter in our catalog ("<name> quran", Shorts of the last 30 days, on any
     channel) and rank them by the views of their 10 most watched Shorts: the reciter leaderboard
  4. add up the views per surah (and per reciter, from the leaderboard), and give the most watched a
     small boost: weight 1.0 (not trending) up to 1.4 (the top one), so our own stats still lead
     (stats weights go from 0.6 to 1.8)
  5. save state/trends.json (read by planner.py) and a readable state/trends.md

Needs YOUTUBE_API_KEY; without it nothing happens and the picks stay as they were.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from collections import defaultdict
from functools import lru_cache

import youtube
from common import CATALOG, STATE, load_json, save_json

TRENDS = STATE / "trends.json"
REPORT = STATE / "trends.md"

REFRESH_DAYS = 6.5    # search again after this long
LOOKBACK_DAYS = 30    # videos published in this window
QUERIES = ["quran recitation", "beautiful quran recitation", "quran shorts", "تلاوة خاشعة", "سورة قرآن"]
BOOST = 0.4           # the top surah/reciter gets weight 1 + BOOST
W_MAX = 1.0 + BOOST
VERSION = 3          # bump when the matching changes: the next run searches again
AL = r"(?:a[lnrstdz]|adh|ash|al)"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _compact(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


def _strip_al(word: str) -> str:
    return re.sub(r"^(?:al|ar|as|an|at|ad|az|ash|adh)-", "", word.lower())


# ---------------------------------------------------------------- matching --
# other common spellings in titles (compared without spaces, hyphens or case)
ALIASES = {
    "yasser-aldosari": ["dossary", "dossari", "dosary", "aldawsari", "dawsari"],
    "mishary-alafasy": ["afasy", "alafasi", "afasi", "mishari"],
    "maher-almuaiqly": ["muaiqli", "mueaqly", "muaiqlee", "maheralmu"],
    "abdulrahman-alsudais": ["sudais", "sudays"],
    "saad-alghamdi": ["ghamdi"],
    "nasser-alqatami": ["qatami", "katami"],
    "ahmed-alajmi": ["alajmi", "alajami"],
    "mahmoud-alhusary": ["husary", "hussary", "husari", "hussari"],
    "minshawi": ["minshawy"],
    "abdulbasit-murattal": ["abdulbaset", "abdelbasset", "abdelbaset", "abdulbasit"],
    "abdulbasit-mujawwad": ["abdulbaset", "abdelbasset", "abdelbaset", "abdulbasit"],
    "saud-alshuraim": ["shuraim", "shuraym"],
    "abu-bakr-alshatri": ["shatri", "shatiri"],
    "hani-alrifai": ["rifai", "alrifai"],
    "idris-abkar": ["abkar"],
}

def reciter_patterns() -> dict[str, list]:
    """{reciter_key: [compiled patterns]} from catalog/reciters.json (English and Arabic names)."""
    out: dict[str, list] = {}
    for r in load_json(CATALOG / "reciters.json", []) or []:
        name = re.sub(r"\(.*?\)", "", r["name_en"]).strip()
        words = name.split()
        latin = {r["tag"], _compact(name), _compact(" ".join(_strip_al(w) for w in words))}
        surname = _compact(_strip_al(words[-1]))
        if len(surname) >= 5 and surname not in {"samad", "abdul"}:
            latin.add(surname)
        pats = [re.compile(re.escape(x)) for x in latin if len(x) >= 5]  # matched on compacted text
        pats += [re.compile(re.escape(x)) for x in ALIASES.get(r["key"], [])]
        ar = r.get("name_ar", "").split()
        ar_alias = {" ".join(ar)}
        if ar and len(ar[-1]) >= 4 and ar[-1] not in {"الصمد"}:
            ar_alias.add(ar[-1])
        out[r["key"]] = [("latin", p) for p in pats] + [("ar", re.compile(re.escape(a))) for a in ar_alias if a]
    return out


def surah_patterns() -> dict[int, list]:
    """{surah: [patterns]}: 'Surah Al-Kahf', 'surat yasin', 'سورة الكهف' and so on."""
    import quran
    out: dict[int, list] = {}
    for n, c in quran.chapters().items():
        words = [w for w in re.split(r"[^a-z]+", _strip_al(c["name_simple"])) if w]
        if not words:
            continue
        stem = r"[\s']*".join(map(re.escape, words))
        if stem.endswith("h"):
            stem += "?"  # Fatihah / Fatiha
        en = re.compile(rf"\bsura[ht]?\s+(?:{AL}\s*)?{stem}\b")
        ar_bare = re.sub(r"^ال", "", c["name_arabic"])
        ar = re.compile(rf"سور[ةه]\s*(?:ال)?{re.escape(ar_bare)}(?![\u0621-\u064A])")
        out[int(n)] = [("en", en), ("ar", ar)]
    return out


def _flat(text: str) -> str:
    """Lower case, Latin letters spelled plainly (Al-Kahf -> al kahf), Arabic diacritics dropped."""
    t = text.lower().replace("\u2019", "'")
    t = re.sub("[\u064B-\u0652\u0670\u0640]", "", t)
    return re.sub(r"[-_]", " ", t)


def match(text: str, rpats: dict, spats: dict) -> tuple[set, set]:
    flat = _flat(text)
    compact = _compact(flat)
    recs = {k for k, ps in rpats.items()
            if any((p.search(compact) if kind == "latin" else p.search(flat)) for kind, p in ps)}
    surahs = {n for n, ps in spats.items() if any(p.search(flat) for _, p in ps)}
    return recs, surahs


def credit(title: str, description: str, rpats: dict, spats: dict) -> tuple[set, set]:
    """The surahs and reciters a video is about: the title decides; the description only fills in when it
    names exactly one (descriptions are often keyword lists naming many surahs, even on Azan clips)."""
    t_recs, t_surahs = match(title, rpats, spats)
    d_recs, d_surahs = match(description, rpats, spats)
    recs = t_recs or (d_recs if len({_tag(r) for r in d_recs}) == 1 else set())
    surahs = t_surahs or (d_surahs if len(d_surahs) == 1 else set())
    if len(surahs) > 2:  # a title listing many surahs is a compilation: not a signal for any one
        surahs = set()
    if len({_tag(r) for r in recs}) > 2:
        recs = set()
    return recs, surahs


@lru_cache(maxsize=None)
def _tag(key: str) -> str:
    """Reciters that share a tag (Abdul Basit murattal and mujawwad) count as one person."""
    tags = {r["key"]: r["tag"] for r in load_json(CATALOG / "reciters.json", []) or []}
    return tags.get(key, key)


# ---------------------------------------------------------------- weights --
def _weights(total: dict) -> dict:
    """Views per value -> 1.0 .. 1.4: the most watched gets 1.4, a quarter of its views about 1.2."""
    top = max(total.values(), default=0)
    if top <= 0:
        return {}
    return {k: round(1.0 + BOOST * math.sqrt(v / top), 3) for k, v in total.items()}


def weights() -> dict[str, dict[str, float]]:
    """{'surah': {'18': 1.3, ...}, 'reciter': {...}}; missing = 1.0. Never raises."""
    try:
        data = load_json(TRENDS, {}) or {}
        return {k: {v: min(max(float(x), 1.0), W_MAX) for v, x in ((data.get("weights") or {}).get(k) or {}).items()}
                for k in ("surah", "reciter")}
    except Exception:
        return {"surah": {}, "reciter": {}}


def weight(kind: str, value, table: dict | None = None) -> float:
    table = table if table is not None else weights()
    return float(table.get(kind, {}).get(str(value), 1.0))


def leaderboard(since: dt.datetime, rpats: dict, spats: dict) -> dict[str, dict]:
    """{tag: {...}}: how each reciter's recent Shorts do across YouTube, from one search per reciter."""
    people: dict[str, dict] = {}
    for r in load_json(CATALOG / "reciters.json", []) or []:
        people.setdefault(r["tag"], {"keys": [], "name": re.sub(r"\s*\(.*?\)", "", r["name_en"])})
        people[r["tag"]]["keys"].append(r["key"])
    board: dict[str, dict] = {}
    for tag, who in people.items():
        try:
            ids = youtube.search_shorts(f"{who['name']} quran", since, max_results=25)
            vids = youtube.stats(ids)
        except youtube.YouTubeError as e:
            print(f"trends: reciter search stopped at {who['name']} ({e})", flush=True)
            return {}  # a half-filled board would favour whoever came first: use none of it
        mine = []
        for vid, v in vids.items():
            recs, _ = credit(v["title"], v["description"], rpats, spats)
            if {_tag(k) for k in recs} == {tag}:  # about this reciter alone
                mine.append({"id": vid, "views": v["views"], "channel": v["channel"], "title": v["title"][:100]})
        mine.sort(key=lambda x: -x["views"])
        board[tag] = {"keys": who["keys"], "name": who["name"], "videos": len(mine),
                      "channels": len({m["channel"] for m in mine}),
                      "top10": sum(m["views"] for m in mine[:10]), "best": mine[0] if mine else None}
    return board


# ------------------------------------------------------------------- main --
def update(force: bool = False) -> str:
    """Search, score and save. Returns a one-line summary for the log."""
    if not youtube.key():
        return "trends: skipped (no YOUTUBE_API_KEY)"
    old = load_json(TRENDS, {}) or {}
    last = old.get("updated")
    if not force and last and old.get("version") == VERSION:
        age = _now() - dt.datetime.fromisoformat(last)
        if age < dt.timedelta(days=REFRESH_DAYS):
            return f"trends: up to date ({age.days} day(s) old, next search in {REFRESH_DAYS - age.total_seconds() / 86400:.1f} days)"
    since = _now() - dt.timedelta(days=LOOKBACK_DAYS)
    ids: list[str] = []
    for q in QUERIES:
        ids += youtube.search_shorts(q, since)
    vids = youtube.stats(ids)
    rpats, spats = reciter_patterns(), surah_patterns()
    by_surah: dict[str, float] = defaultdict(float)
    by_rec: dict[str, float] = defaultdict(float)
    count_s: dict[str, int] = defaultdict(int)
    count_r: dict[str, int] = defaultdict(int)
    top = []
    for vid, v in vids.items():
        recs, surahs = credit(v["title"], v["description"], rpats, spats)
        for r in recs:
            by_rec[r] += v["views"] / len(recs)
            count_r[r] += 1
        for s in surahs:
            by_surah[str(s)] += v["views"] / len(surahs)
            count_s[str(s)] += 1
        top.append({"id": vid, "title": v["title"][:120], "channel": v["channel"], "views": v["views"],
                    "reciters": sorted(recs), "surahs": sorted(surahs)})
    top.sort(key=lambda x: -x["views"])
    board = leaderboard(since, rpats, spats)
    rec_weights = _weights(by_rec)
    if board:  # a direct search per reciter says more than reciters named in general results
        per_key = {k: b["top10"] for b in board.values() for k in b["keys"] if b["top10"] > 0}
        rec_weights = _weights(per_key)
    data = {
        "updated": _now().isoformat(timespec="seconds"),
        "version": VERSION,
        "videos": len(vids),
        "weights": {"surah": _weights(by_surah), "reciter": rec_weights},
        "leaderboard": board,
        "views": {"surah": {k: round(v) for k, v in by_surah.items()}, "reciter": {k: round(v) for k, v in by_rec.items()}},
        "counts": {"surah": dict(count_s), "reciter": dict(count_r)},
        "top": top[:30],
    }
    save_json(TRENDS, data)
    REPORT.write_text(report(data), encoding="utf-8")
    lead = max(board.values(), key=lambda b: b["top10"])["name"] if board else "none"
    return (f"trends: {len(vids)} popular Quran Shorts read, {len(by_surah)} surahs found, "
            f"{len(board)} reciters compared (top: {lead}) - see state/trends.md")


def report(data: dict) -> str:
    import quran
    try:
        names = {str(n): c["name_simple"] for n, c in quran.chapters().items()}
    except Exception:
        names = {}
    recs = {r["key"]: r["name_en"] for r in load_json(CATALOG / "reciters.json", []) or []}
    lines = [f"# What is popular on YouTube (updated {data['updated'][:10]})", "",
             f"The {data['videos']} most viewed Quran Shorts of the last {LOOKBACK_DAYS} days. Surahs and reciters "
             "that show up in them get a small boost when the next videos are picked (weight 1.0 to "
             f"{W_MAX:.1f}; our own post stats still count more).", ""]
    board = data.get("leaderboard") or {}
    if board:
        lines += ["## Reciter leaderboard", "",
                  "One YouTube search per reciter: their Shorts from the last 30 days on any channel. Ranked by the "
                  "views of their 10 most watched; the weight is the boost when picking the next reciter.", "",
                  "| | Reciter | Shorts found | Channels | Top 10 views | Weight | Most watched |", "|---|---|---|---|---|---|---|"]
        ranked = sorted(board.values(), key=lambda b: -b["top10"])
        for n, b in enumerate(ranked, 1):
            w = data["weights"]["reciter"].get(b["keys"][0], 1.0)
            best = b.get("best")
            link = (f"[{best['views']:,} views](https://youtube.com/shorts/{best['id']}) - {best['channel'].replace('|', '/')}"
                    if best else "")
            lines.append(f"| {n} | {b['name']} | {b['videos']} | {b['channels']} | {b['top10']:,} | {w:.2f} | {link} |")
        lines.append("")
    for kind, title, label in (("surah", "Surahs", lambda k: f"{k}. {names.get(k, '')}"),
                               ("reciter", "Reciters", lambda k: recs.get(k, k))):
        if kind == "reciter" and board:
            continue
        w = data["weights"].get(kind) or {}
        if not w:
            continue
        lines += [f"## {title}", "", "| | Videos | Views | Weight |", "|---|---|---|---|"]
        for k in sorted(w, key=lambda k: -data["views"][kind][k]):
            lines.append(f"| {label(k)} | {data['counts'][kind][k]} | {data['views'][kind][k]:,} | {w[k]:.2f} |")
        lines.append("")
    lines += ["## Top videos", "", "| Views | Title | Channel |", "|---|---|---|"]
    for t in data["top"][:15]:
        title = t["title"].replace("|", "/")
        lines.append(f"| {t['views']:,} | [{title}](https://youtube.com/shorts/{t['id']}) | {t['channel'].replace('|', '/')} |")
    return "\n".join(lines) + "\n"
