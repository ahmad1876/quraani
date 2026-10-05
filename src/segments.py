"""Split ayahs into on-screen phrases and time them against the recitation."""
from __future__ import annotations

import math

import numpy as np

import audio
import quran

MAX_LETTERS = 62     # what fits comfortably in ~3 lines at a readable size
MIN_LETTERS = 14     # avoid flashing tiny fragments
LEAD = 0.15          # show text slightly before the reciter starts it
TARGET_SEC = 8.5     # aim for phrases of about this many seconds on screen
GAP = 0.4            # a breath this long counts as a natural break
STRONG = set("\u06D7\u06DA\u06D6")  # qala / jeem / sala: natural stopping points


def _letters(words: list[dict]) -> int:
    return sum(quran.letters(w["text"]) for w in words)


def split_words(words: list[dict], gaps: list[float] | None = None, duration: float = 0.0,
                extra_natural: int = 0) -> list[list[int]]:
    """Return groups of word indices for on-screen phrases.

    Breaks prefer waqf (pause) marks and the reciter's own breathing gaps. Long
    ayahs are split by length and by time, so each phrase stays on screen for
    roughly 4-9 seconds and the text follows the recitation closely.
    """
    n = len(words)
    if n == 0:
        return []
    total = _letters(words)
    letters_min = max(1, math.ceil(total / (MAX_LETTERS - 16)))
    breaks_natural = sum(1 for i in range(n - 1) if words[i]["waqf"] or (gaps and gaps[i] >= GAP))
    breaks_natural += extra_natural
    want_time = round(duration / TARGET_SEC) if duration else 1
    soft = 0
    if duration and duration / (breaks_natural + 1) > 13.0:
        # very slow recitation: also allow breaks before a new clause (wa-/fa-)
        soft = sum(1 for i in range(n - 1) if _bare(words[i + 1]["text"])[:1] in ("و", "ف")
                   and _bare(words[i]["text"]) not in NO_END)
    want = max(letters_min, min(want_time, breaks_natural + soft + 1))
    want = min(want, max(1, total // MIN_LETTERS), n)
    if want == 1:
        return [list(range(n))]
    # candidate break after word i (higher is better)
    score = [0.0] * (n - 1)
    soft_ok = [False] * (n - 1)
    for i in range(n - 1):
        natural = False
        if words[i]["waqf"]:
            score[i] += 3.0 if words[i]["waqf"] in STRONG else 2.0
            natural = True
        if gaps is not None and gaps[i] >= GAP:
            score[i] += min(gaps[i], 1.2) * 2.0
            natural = True
        if _bare(words[i]["text"]) in NO_END:
            score[i] -= 4.0
        nxt = _bare(words[i + 1]["text"])
        if nxt[:1] in ("و", "ف") and len(nxt) > 2:
            soft_ok[i] = True
        if not natural:
            score[i] += -0.2 if soft_ok[i] else -5.0
    cum, acc = [], 0
    for w in words:
        acc += quran.letters(w["text"])
        cum.append(acc)
    # dynamic programming over break positions: balanced lengths + good break points
    import functools

    @functools.lru_cache(maxsize=None)
    def best(start: int, parts: int):
        if parts == 1:
            L = cum[-1] - (cum[start - 1] if start else 0)
            return (-_len_cost(L, total / want), ())
        res = None
        for i in range(start, n - parts + 1):
            L = cum[i] - (cum[start - 1] if start else 0)
            natural_end = bool(words[i]["waqf"]) or (gaps is not None and gaps[i] >= GAP) or soft_ok[i]
            if L < (MIN_LETTERS * 0.6 if natural_end else MIN_LETTERS):
                continue
            sub = best(i + 1, parts - 1)
            if sub is None:
                continue
            val = -_len_cost(L, total / want) + score[i] + sub[0]
            if res is None or val > res[0]:
                res = (val, (i,) + sub[1])
        return res

    found = best(0, want)
    if not found:
        return [list(range(n))]
    cuts = list(found[1])
    groups, prev = [], 0
    for c in cuts:
        groups.append(list(range(prev, c + 1)))
        prev = c + 1
    groups.append(list(range(prev, n)))
    return groups


NO_END = {"لا", "ما", "من", "في", "على", "الي", "عن", "ان", "قد", "يا", "ثم", "او", "لم", "لن", "الذي",
          "التي", "الذين", "اذا", "اذ", "كل", "بين", "حتي", "لكن", "بل", "هل", "كي", "لو", "ولا", "وما",
          "ومن", "وفي", "وان", "فلا", "فان", "لقد", "وقد", "اما", "الا", "ولم", "يوم", "رب"}


def _bare(t: str) -> str:
    import unicodedata
    t = "".join(c for c in unicodedata.normalize("NFKD", t) if unicodedata.category(c) != "Mn")
    t = t.replace("\u0640", "").replace("ٱ", "ا").replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    t = t.replace("ى", "ي").replace("ۥ", "").replace("ۦ", "")
    return "".join(c for c in t if "\u0621" <= c <= "\u064A")


def _len_cost(L: float, ideal: float) -> float:
    over = max(0.0, L - MAX_LETTERS)
    return abs(L - ideal) / max(ideal, 1) * 1.5 + over * 2.5


def build_units(ch: int, a: int, b: int, t: dict, env: np.ndarray, raw0: float,
                cut0: float, cut1: float, total: float | None = None) -> list[dict]:
    """Return [{text, start, end, ayah}] with times relative to the clip start."""
    units = []
    for k in range(a, b + 1):
        v = quran.verse(f"{ch}:{k}")
        words = v["words"]
        tv = t["verses"][k]
        a0 = cut0 if k == a else tv["start"]
        a1 = cut1 if k == b else tv["end"]
        seg = {p: (s, e) for p, s, e in (tv["words"] or [])}
        wt = _word_times(words, seg, a0, a1) if seg else None
        gaps = None
        if wt:
            gaps = [max(0.0, wt[i + 1][0] - wt[i][1]) for i in range(len(words) - 1)] + [0.0]
        dur_k = (wt[-1][1] - wt[0][0]) if wt else (a1 - a0)
        extra = 0
        if not wt:  # breaths beyond the marked pauses hint at extra phrase breaks
            n_p = len(audio.pauses(env, a0 - raw0 + 0.4, a1 - raw0 - 0.4, min_len=0.22))
            extra = max(0, n_p - sum(1 for w in words[:-1] if w["waqf"]))
        groups = split_words(words, gaps, dur_k, extra)
        if wt:
            starts = [wt[g[0]][0] for g in groups]
        else:
            starts = _ayah_level_starts(groups, words, env, raw0, a0, a1)
        share, acc = [], 0           # how far through the ayah (by letters) each word ends
        for w in words:
            acc += quran.letters(w["text"])
            share.append(acc)
        for gi, g in enumerate(groups):
            text = " ".join(words[i]["text"] for i in g)
            if gi == len(groups) - 1:
                text += "\u00a0" + quran.ayah_marker(k)
            units.append({"text": text, "abs_start": max(starts[gi], a0 if gi == 0 else starts[gi]), "ayah": k,
                          "f1": share[g[-1]] / max(1, acc)})
    # convert to clip-relative display windows
    dur = total if total else cut1 - cut0
    for i, u in enumerate(units):
        s = 0.0 if i == 0 else max(0.0, u["abs_start"] - cut0 - LEAD)
        u["start"] = s
    for i, u in enumerate(units):
        u["end"] = units[i + 1]["start"] if i + 1 < len(units) else dur
    # merge units that would flash for less than 0.9 s
    merged = []
    for u in units:
        if merged and (u["end"] - u["start"]) < 0.9 and merged[-1]["ayah"] == u["ayah"]:
            merged[-1]["text"] += " " + u["text"]
            merged[-1]["end"] = u["end"]
            merged[-1]["f1"] = u["f1"]
        else:
            merged.append(dict(u))
    return merged


def _word_times(words, seg, a0, a1):
    """Start/end per word from quran.com segments, interpolating any gaps."""
    n = len(words)
    times = [seg.get(w["pos"]) for w in words]
    known = [i for i, x in enumerate(times) if x]
    if not known:
        return None
    for i in range(n):
        if times[i]:
            continue
        prev = max([j for j in known if j < i], default=None)
        nxt = min([j for j in known if j > i], default=None)
        lo = times[prev][1] if prev is not None else a0
        hi = times[nxt][0] if nxt is not None else a1
        span = max(hi - lo, 0.05)
        cnt = (nxt if nxt is not None else n) - (prev if prev is not None else -1) - 1
        pos = i - (prev if prev is not None else -1)
        times[i] = (lo + span * (pos - 1) / cnt, lo + span * pos / cnt)
    return times


def _ayah_level_starts(groups, words, env, raw0, a0, a1):
    """Estimate phrase starts inside one ayah using breath pauses (ayah-level timing only)."""
    if len(groups) == 1:
        return [a0]
    lens = [_letters([words[i] for i in g]) for g in groups]
    total = sum(lens)
    span = a1 - a0
    expected, acc = [], 0
    for L in lens[:-1]:
        acc += L
        expected.append(a0 + span * acc / total)
    found = audio.pauses(env, a0 - raw0 + 0.4, a1 - raw0 - 0.4, min_len=0.22)
    mids = [((s + e) / 2 + raw0, e + raw0) for s, e in found]
    tol = max(1.0, 0.11 * span)
    starts, used, last = [a0], set(), a0
    for ex in expected:
        cands = [(abs(m - ex), j, end) for j, (m, end) in enumerate(mids)
                 if j not in used and m > last + 0.6 and abs(m - ex) <= tol]
        if cands:
            _, j, end = min(cands)
            used.add(j)
            st = end  # phrase starts as the reciter resumes
        else:
            st = ex
        min_gap = 0.45 * span * lens[len(starts) - 1] / total
        st = max(st, last + max(0.6, min_gap))
        starts.append(st)
        last = st
    return starts
