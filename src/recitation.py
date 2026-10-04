"""Reciter timing data: quran.com (word-level) and mp3quran.net (ayah-level).

Everything is normalised to seconds:
  {"audio_url": str, "kind": "word"|"ayah",
   "verses": {ayah_no: {"start": s, "end": s, "words": [(pos, s, e), ...] | None}}}
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from common import CATALOG, get_json

QDC = "https://api.qurancdn.com/api/qdc/audio/reciters/{id}/audio_files?chapter={ch}&segments=true"
MP3Q_TIMING = "https://www.mp3quran.net/api/v3/ayat_timing?surah={ch}&read={read}"


@lru_cache(maxsize=None)
def reciters() -> dict[str, dict]:
    data = json.loads((CATALOG / "reciters.json").read_text(encoding="utf-8"))
    return {r["key"]: r for r in data}


def _qdc(rid: int, ch: int) -> dict:
    data = get_json(QDC.format(id=rid, ch=ch))
    af = data["audio_files"][0]
    verses = {}
    for v in af["verse_timings"]:
        s, a = v["verse_key"].split(":")
        if int(s) != ch:
            continue
        words = []
        for seg in v.get("segments") or []:
            if len(seg) >= 3:
                pos, ws, we = int(seg[0]), float(seg[1]) / 1000, float(seg[2]) / 1000
                if we > ws:
                    words.append((pos, ws, we))
        verses[int(a)] = {
            "start": float(v["timestamp_from"]) / 1000,
            "end": float(v["timestamp_to"]) / 1000,
            "words": sorted(words) or None,
        }
    url = af["audio_url"]
    scheme, rest = url.split("://", 1)
    url = scheme + "://" + re.sub(r"/{2,}", "/", rest)
    return {"audio_url": url, "kind": "word", "verses": verses}


def _mp3q(read: int, folder: str, ch: int) -> dict:
    data = get_json(MP3Q_TIMING.format(ch=ch, read=read))
    verses = {}
    for v in data:
        a = int(v["ayah"])
        if a < 1:
            continue
        verses[a] = {"start": v["start_time"] / 1000, "end": v["end_time"] / 1000, "words": None}
    url = folder.rstrip("/") + f"/{ch:03d}.mp3"
    return {"audio_url": url, "kind": "ayah", "verses": verses}


@lru_cache(maxsize=None)
def timing(reciter_key: str, ch: int) -> dict:
    r = reciters()[reciter_key]
    src = r["source"]
    if src["type"] == "qdc":
        return _qdc(src["id"], ch)
    if src["type"] == "mp3q":
        return _mp3q(src["read"], src["folder"], ch)
    raise ValueError(f"unknown source {src}")


def fit_span(t: dict, start: int, end: int, max_sec: float, min_sec: float = 8.0, need: int = 0):
    """Longest run of whole ayahs from `start` (<= end) whose audio fits in max_sec.

    Returns (start_ayah, last_ayah, t0, t1) or None. Never cuts inside an ayah.
    """
    vs = t["verses"]
    if start not in vs:
        return None
    t0 = _first_sound(vs[start])
    best = None
    for a in range(start, end + 1):
        if a not in vs:
            break
        t1 = _last_sound(vs[a])
        if t1 - t0 <= max_sec:
            best = (start, a, t0, t1)
        else:
            break
    if best and best[3] - best[2] >= min_sec and best[1] >= need:
        return best
    return None


def _first_sound(v: dict) -> float:
    if v.get("words"):
        return max(v["start"], v["words"][0][1]) if v["words"][0][1] >= v["start"] - 0.5 else v["start"]
    return v["start"]


def _last_sound(v: dict) -> float:
    if v.get("words"):
        return max(v["words"][-1][2], v["words"][-1][1] + 0.2)
    return v["end"]
