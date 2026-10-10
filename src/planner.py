"""Choose what to post next: popular passages x popular reciters, without repeats."""
from __future__ import annotations

import json
import random

import make
import recitation
import stats
import trends
from common import CATALOG

PRIORITY_WEIGHT = {1: 3.0, 2: 1.6, 3: 0.8}
TIER_WEIGHT = {1: 3.0, 2: 1.7, 3: 0.8}


def passages() -> list[dict]:
    return json.loads((CATALOG / "passages.json").read_text(encoding="utf-8"))


def _weighted_order(items, weight, rng):
    # Efraimidis-Spirakis: random order biased by weight
    keyed = [(rng.random() ** (1.0 / max(weight(i), 1e-6)), i) for i in items]
    return [i for _, i in sorted(keyed, key=lambda x: -x[0])]


def choose(n: int, history: list[dict], rng: random.Random | None = None,
           passage_gap: int = 80, reciter_gap: int = 3) -> list[tuple[dict, str]]:
    """Return n (passage, reciter_key) pairs that fit the length limit."""
    rng = rng or random.Random()
    recs = recitation.reciters()
    learned = stats.weights()  # from post stats (state/stats_weights.json); empty = neutral
    trending = trends.weights()  # from popular Quran Shorts on YouTube (state/trends.json); empty = neutral
    recent_p = {h["passage"] for h in history[-passage_gap:]}
    recent_r = [h["reciter"] for h in history[-reciter_gap:]]
    used_pairs = {(h["passage"], h["reciter"]) for h in history}
    picks: list[tuple[dict, str]] = []
    ps = [p for p in passages() if p["id"] not in recent_p] or passages()
    p_weight = lambda p: (PRIORITY_WEIGHT.get(p["priority"], 1.0) * stats.weight("theme", p.get("theme", ""), learned)
                          * trends.weight("surah", p["surah"], trending))
    r_weight = lambda k: (TIER_WEIGHT.get(recs[k]["tier"], 1.0) * stats.weight("reciter", k, learned)
                          * trends.weight("reciter", k, trending))
    for p in _weighted_order(ps, p_weight, rng):
        if len(picks) >= n:
            break
        if any(p["id"] == q["id"] for q, _ in picks):
            continue
        taken_r = recent_r + [r for _, r in picks]
        for rk in _weighted_order(list(recs), r_weight, rng):
            if rk in taken_r or (p["id"], rk) in used_pairs:
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
