"""Real nature footage for backgrounds.

Default source: catalog/footage.json, a library of free stock clips (Pexels and
Mixkit) that were screened for people, animals and symbols. Clips are streamed
from the original free CDNs on demand; nothing is re-hosted. Optional sources: a
local folder (QURAANI_LOCAL_FOOTAGE) or the Pexels API (PEXELS_API_KEY).

Every clip goes through a small person/animal detector before first use.
"""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import urllib.parse
from collections import defaultdict
from pathlib import Path

from common import CACHE, CATALOG, download, ffprobe_duration, http_get, load_json, save_json

BLOCK = re.compile(
    r"\b(man|men|woman|women|person|people|girl|boy|child|children|kid|baby|couple|family|friends|"
    r"hiker|hiking|tourist|traveler|traveller|surfer|swimmer|skier|snowboard|climber|runner|cyclist|"
    r"biker|rider|fisherman|farmer|worker|crowd|silhouette|hand|hands|feet|foot|face|portrait|selfie|"
    r"model|dancer|dance|yoga|bikini|swimsuit|wedding|bride|"
    r"dog|cat|bird|birds|horse|cow|cattle|sheep|goat|camel|deer|elk|fish|whale|dolphin|seal|bear|"
    r"duck|swan|eagle|seagull|gull|butterfly|bee|insect|animal|animals|wildlife|pet|lion|elephant|"
    r"church|cross|temple|buddha|statue|idol|pagoda|shrine|christmas|halloween|cemetery|grave|"
    r"wine|beer|alcohol|cocktail|bar|party|club|car|cars|traffic|road|highway|city|street|flag)\b",
    re.I,
)

PEXELS = "https://api.pexels.com/videos/search"
QUERIES = ["clouds timelapse", "sunset clouds", "foggy mountains", "misty forest", "waterfall", "ocean waves aerial",
           "calm lake", "desert dunes", "night sky stars", "snowy mountains", "green hills", "storm clouds"]


class Clip(dict):
    """{'path', 'duration', 'source', 'id', 'credit', 'url', 'theme'}"""


def _history_ids(history: list[dict]) -> set[str]:
    return {c for h in history for c in h.get("clips", [])}


def pick_clips(n: int, min_len: float, history: list[dict], *, seed: int | None = None,
               local_dir: str | None = None) -> list[Clip]:
    rng = random.Random(seed)
    used = _history_ids(history[-60:])
    local_dir = local_dir or os.environ.get("QURAANI_LOCAL_FOOTAGE")
    if local_dir:
        return _local(n, min_len, used, rng, Path(local_dir))
    if (CATALOG / "footage.json").exists():
        recent_themes = [h.get("footage_theme") for h in history[-3:] if h.get("footage_theme")]
        return _from_manifest(n, min_len, used, rng, recent_themes)
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if key:
        return _pexels(n, min_len, used, rng, key)
    raise RuntimeError("No footage source: catalog/footage.json is missing")


# ------------------------------------------------------------------ library --
def clips_by_ids(ids: list[str]) -> list[Clip]:
    """The given library clips, in order (used to re-render a video with the same footage)."""
    lib = {c["id"]: c for c in json.loads((CATALOG / "footage.json").read_text(encoding="utf-8"))}
    out = []
    for cid in ids:
        c = lib.get(cid)
        if not c:
            raise RuntimeError(f"clip {cid} is not in the footage library")
        dest = CACHE / "footage" / (cid.replace(":", "_") + ".mp4")
        download(c["url"], dest)
        out.append(Clip(path=str(dest), duration=float(c.get("duration") or ffprobe_duration(dest)),
                        source=c.get("src", ""), id=cid, credit=c.get("credit", ""), url=c.get("page", ""),
                        theme=c["theme"]))
    return out


def _bad_ids() -> set[str]:
    return set(load_json(CACHE / "footage_rejected.json", []) or [])


def _mark_bad(cid: str) -> None:
    bad = sorted(_bad_ids() | {cid})
    save_json(CACHE / "footage_rejected.json", bad)


def _from_manifest(n, min_len, used, rng, recent_themes) -> list[Clip]:
    lib = [c for c in json.loads((CATALOG / "footage.json").read_text(encoding="utf-8")) if c.get("ok", True)]
    bad = _bad_ids()
    by_theme: dict[str, list[dict]] = defaultdict(list)
    for c in lib:
        if c["id"] not in bad:
            by_theme[c["theme"]].append(c)
    themes = [t for t in by_theme if len(by_theme[t]) >= n]
    rng.shuffle(themes)
    themes.sort(key=lambda t: t in recent_themes)  # fresh moods first
    for theme in themes:
        pool = by_theme[theme][:]
        rng.shuffle(pool)
        pool.sort(key=lambda c: (c["id"] in used, c.get("duration", 0) * 2 < min_len))
        picked: list[Clip] = []
        for c in pool:
            dest = CACHE / "footage" / (c["id"].replace(":", "_") + ".mp4")
            try:
                download(c["url"], dest)
            except Exception:
                continue
            if not c.get("screened") and frames_have_living_in_video(str(dest)):
                _mark_bad(c["id"])
                dest.unlink(missing_ok=True)
                continue
            dur = c.get("duration") or ffprobe_duration(dest)
            picked.append(Clip(path=str(dest), duration=float(dur), source=c.get("src", ""), id=c["id"],
                               credit=c.get("credit", ""), url=c.get("page", ""), theme=theme))
            if len(picked) == n:
                return picked
    raise RuntimeError("could not assemble clips from the footage library")


# ------------------------------------------------------------------- local --
def _local(n, min_len, used, rng, folder: Path) -> list[Clip]:
    files = sorted(p for p in folder.glob("*.mp4"))
    rng.shuffle(files)
    fresh = [p for p in files if f"local:{p.name}" not in used]
    fresh += [p for p in files if p not in fresh]  # top up with already-used clips if needed
    out = []
    for p in fresh:
        if frames_have_living_in_video(str(p)):
            continue
        d = ffprobe_duration(p)
        if d >= min(min_len, 6):
            out.append(Clip(path=str(p), duration=d, source="local", id=f"local:{p.name}", credit="", url="",
                            theme="local"))
        if len(out) == n:
            break
    if len(out) < n:
        raise RuntimeError("not enough local clips")
    return out


# --------------------------------------------------------- Pexels API (opt) --
def _pexels(n, min_len, used, rng, key) -> list[Clip]:
    out: list[Clip] = []
    queries = QUERIES[:]
    rng.shuffle(queries)
    for q in queries:
        if len(out) >= n:
            break
        url = f"{PEXELS}?{urllib.parse.urlencode({'query': q, 'orientation': 'portrait', 'per_page': 40})}"
        try:
            data = json.loads(http_get(url, headers={"Authorization": key}))
        except Exception:
            continue
        for v in data.get("videos", []):
            if len(out) >= n:
                break
            vid = f"pexels:{v['id']}"
            slug = v.get("url", "").rstrip("/").split("/")[-1].replace("-", " ")
            if vid in used or BLOCK.search(slug) or v.get("duration", 0) < 6:
                continue
            f = _best_file(v.get("video_files", []))
            if not f:
                continue
            dest = CACHE / "footage" / f"pexels_{v['id']}.mp4"
            try:
                download(f["link"], dest)
            except Exception:
                continue
            if frames_have_living_in_video(str(dest)):
                dest.unlink(missing_ok=True)
                continue
            out.append(Clip(path=str(dest), duration=float(v["duration"]), source="pexels", id=vid,
                            credit=v.get("user", {}).get("name", ""), url=v.get("url", ""), theme=q))
    if len(out) < n:
        raise RuntimeError(f"only found {len(out)} clean clips on Pexels")
    return out


def _best_file(files: list[dict]):
    portrait = [f for f in files if (f.get("height") or 0) > (f.get("width") or 0)
                and f.get("file_type") == "video/mp4" and f.get("link")]
    good = sorted([f for f in portrait if f["height"] >= 1920], key=lambda f: f["height"])
    if good:
        return good[0]
    ok = sorted([f for f in portrait if f["height"] >= 1280], key=lambda f: -f["height"])
    return ok[0] if ok else None


# ---------------------------------------------------------------- detector --
def frames_have_living_in_video(path: str, fps: float = 2.0) -> bool:
    """Full check of a clip: ~2 frames per second through the detector."""
    try:
        import numpy as np
        import detect
    except Exception:
        return False
    w, h = 360, 640
    try:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf",
                              f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
                              "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True, check=True).stdout
    except Exception:
        return False
    frame = w * h * 3
    for k in range(len(raw) // frame):
        img = np.frombuffer(raw[k * frame:(k + 1) * frame], dtype=np.uint8).reshape(h, w, 3)
        if detect.find_living(img, threshold=0.25):
            return True
    return False
