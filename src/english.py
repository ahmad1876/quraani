"""English translation shown under the Arabic, split to follow the on-screen phrases.

The Saheeh International translation comes per ayah. When an ayah is shown as
several Arabic phrases, the English is cut into the same number of pieces at
the punctuation or clause break closest to where the Arabic phrase ends (by
share of the ayah), so the meaning moves along with the recitation.
"""
from __future__ import annotations

import re

import quran
from captions import plain

# a new clause often starts with one of these: a good place to cut the English
CLAUSE_START = {"and", "but", "so", "then", "who", "whom", "which", "that", "indeed", "or", "for", "while",
                "when", "if", "nor", "until", "except", "whoever", "those", "what", "say", "unless", "because"}
MIN_WORDS = 3


def screen_text(t: str) -> str:
    """Translation tidied for the screen: no explanatory i.e. notes, brackets or quote marks."""
    t = re.sub(r"\[\s*i\.e\.[^\]]*\]", "", t)
    t = re.sub(r"\(\s*i\.e\.[^)]*\)", "", t)
    t = t.replace("[", "").replace("]", "").replace('"', "")
    t = plain(t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip()
    return re.sub(r"[\s,;:-]+$", "", t)


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    for w in text.split(" "):
        if w == "-" and out:          # keep a dash with the clause it closes
            out[-1] += " -"
        elif w:
            out.append(w)
    return out


def _quality(tok: str, nxt: str) -> float:
    q = 0.0
    if tok.endswith((".", "!", "?")):
        q = 3.0
    elif tok.endswith((";", ":")):
        q = 2.6
    elif tok.endswith(" -"):
        q = 2.4
    elif tok.endswith(","):
        q = 2.0
    if re.sub(r"[^a-z]", "", nxt.lower()) in CLAUSE_START:
        q += 1.0
    return q


def split(text: str, bounds: list[float]) -> list[str]:
    """Cut text into len(bounds)+1 pieces; bounds are the cumulative shares where pieces should end."""
    parts = len(bounds) + 1
    toks = _tokens(text)
    if parts == 1 or len(toks) < parts * MIN_WORDS:
        return [text] * parts            # too short to split: keep the whole ayah on every phrase
    total = sum(len(t) + 1 for t in toks)
    cum, acc = [], 0
    for t in toks:
        acc += len(t) + 1
        cum.append(acc / total)
    n = len(toks)
    quality = [_quality(toks[i], toks[i + 1]) for i in range(n - 1)]
    best: dict[tuple[int, int], tuple[float, tuple[int, ...]]] = {}

    def solve(start: int, k: int):
        """Best cuts for bounds[k:] using tokens from `start`."""
        if k == len(bounds):
            return (0.0, ()) if n - start >= MIN_WORDS else None
        key = (start, k)
        if key in best:
            return best[key]
        res = None
        for i in range(start + MIN_WORDS - 1, n - 1):
            if n - (i + 1) < MIN_WORDS * (len(bounds) - k):
                break
            sub = solve(i + 1, k + 1)
            if sub is None:
                continue
            cost = 12.0 * abs(cum[i] - bounds[k]) - quality[i] + sub[0]
            if res is None or cost < res[0]:
                res = (cost, (i,) + sub[1])
        best[key] = res
        return res

    found = solve(0, 0)
    if not found:
        return [text] * parts
    pieces, prev = [], 0
    for c in list(found[1]) + [n - 1]:
        piece = " ".join(toks[prev:c + 1])
        pieces.append(re.sub(r"[\s,;:-]+$", "", piece))
        prev = c + 1
    return pieces


def attach(units: list[dict], chapter: int) -> None:
    """Add 'en' to every unit (units carry 'ayah' and 'f1', the share of the ayah they end at)."""
    by_ayah: dict[int, list[dict]] = {}
    for u in units:
        by_ayah.setdefault(u["ayah"], []).append(u)
    for k, us in by_ayah.items():
        text = screen_text(quran.verse(f"{chapter}:{k}")["translation"])
        bounds = [u.get("f1", (i + 1) / len(us)) for i, u in enumerate(us[:-1])]
        for u, piece in zip(us, split(text, bounds)):
            u["en"] = piece
