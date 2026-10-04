"""Assemble background clips, text overlays and recitation into a clean 9:16 MP4."""
from __future__ import annotations

import random
from pathlib import Path

import numpy as np
from PIL import Image

from common import CACHE, run

W, H, FPS = 1080, 1920, 30
XF = 0.8  # crossfade between background clips


def grade_png() -> Path:
    """Soft darkening so white text stays readable on bright skies."""
    p = CACHE / "grade_v3.png"
    if p.exists():
        return p
    y = np.linspace(0, 1, H)[:, None]
    x = np.linspace(-1, 1, W)[None, :]
    top = np.clip(1 - y / 0.36, 0, 1) ** 1.6 * 0.55
    bottom = np.clip((y - 0.70) / 0.30, 0, 1) ** 1.4 * 0.50
    cy = 900 / H
    radial = np.exp(-(((x / 1.25) ** 2) + ((y - cy) / 0.26) ** 2)) * 0.36
    base = 0.20
    a = np.clip(base + top + bottom + radial, 0, 0.85)
    rgba = np.zeros((H, W, 4), dtype=np.uint8)
    rgba[..., 3] = (a * 255).astype(np.uint8)
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(p)
    return p


def clip_luma(path: str) -> float:
    """Average brightness (0-1) of a clip from a few small frames."""
    import subprocess
    try:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", "fps=1,scale=64:114,format=gray",
                              "-frames:v", "8", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
        return float(np.frombuffer(raw, dtype=np.uint8).mean() / 255) if raw else 0.4
    except Exception:
        return 0.4


def tone(luma: float) -> str:
    """Per-clip tone so dark night/forest clips are lifted and bright skies are calmed."""
    if luma < 0.16:
        return "eq=gamma=1.28:brightness=0.03:saturation=1.05"
    if luma < 0.28:
        return "eq=gamma=1.12:brightness=0.01:saturation=1.0"
    if luma > 0.58:
        return "eq=contrast=1.03:saturation=0.92:brightness=-0.08"
    return "eq=contrast=1.04:saturation=0.94:brightness=-0.03"


def plan_cuts(duration: float, unit_starts: list[float], n: int) -> list[float]:
    """Visible length of each background clip, switching near phrase changes."""
    if n <= 1:
        return [duration]
    cuts, last = [], 0.0
    for k in range(1, n):
        target = duration * k / n
        cands = [s for s in unit_starts if last + 4 < s < duration - 4]
        c = min(cands, key=lambda s: abs(s - target)) if cands else target
        if abs(c - target) > duration / (2 * n):
            c = target
        cuts.append(c)
        last = c
    edges = [0.0] + cuts + [duration]
    return [edges[i + 1] - edges[i] for i in range(n)]


def build(clips: list[dict], audio_wav: Path, ov: dict, out_mp4: Path, duration: float,
          seed: int = 0, preset: str = "medium") -> Path:
    rng = random.Random(seed)
    unit_starts = [u["start"] for u in ov["units"]]
    segs = plan_cuts(duration, unit_starts, len(clips))
    args = ["ffmpeg", "-v", "error", "-y"]
    fl = []
    # background inputs
    for i, (c, s) in enumerate(zip(clips, segs)):
        need = s + (XF if i < len(clips) - 1 else 0) + 0.1
        src = float(c["duration"])
        speed = max(1.0, need / max(src - 0.3, 0.5))
        loop = speed > 2.0
        if loop:
            speed = 1.6
            args += ["-stream_loop", "-1"]
        span = need / speed
        start = 0.0 if loop or src - span <= 0.3 else rng.uniform(0, max(0.0, src - span - 0.2))
        args += ["-i", c["path"]]
        fl.append(
            f"[{i}:v]trim=start={start:.3f}:duration={span:.3f},setpts=(PTS-STARTPTS)*{speed:.4f},"
            f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},fps={FPS},setsar=1,"
            f"{tone(clip_luma(c['path']))},format=yuv420p[b{i}]"
        )
    n = len(clips)
    last = "b0"
    acc = segs[0]
    for i in range(1, n):
        fl.append(f"[{last}][b{i}]xfade=transition=fade:duration={XF}:offset={acc:.3f}[x{i}]")
        last = f"x{i}"
        acc += segs[i]
    idx = n
    args += ["-i", str(grade_png())]
    fl.append(f"[{last}][{idx}:v]overlay=0:0:format=auto,format=yuv420p[g]")
    idx += 1
    cur = "g"
    hdr = ov["header"]
    args += ["-i", hdr["png"]]
    # a single still frame; overlay repeats it for the whole video (visible from frame 0 for the cover)
    fl.append(f"[{cur}][{idx}:v]overlay={hdr['x']}:{hdr['y']}:eof_action=repeat[h0]")
    cur = "h0"
    idx += 1
    if ov.get("handle"):
        hl = ov["handle"]
        args += ["-i", hl["png"]]
        fl.append(f"[{cur}][{idx}:v]overlay={hl['x']}:{hl['y']}:eof_action=repeat[hh]")
        cur = "hh"
        idx += 1
    for k, u in enumerate(ov["units"]):
        d = max(0.3, u["end"] - u["start"])
        fin = min(0.28, d / 4)
        fout = min(0.22, d / 4)
        is_last = k == len(ov["units"]) - 1
        args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{d:.3f}", "-i", u["png"]]
        chain = f"[{idx}:v]format=rgba"
        if k > 0:  # first phrase is fully visible on frame 0 (cover + hook)
            chain += f",fade=t=in:st=0:d={fin:.2f}:alpha=1"
        if not is_last:
            chain += f",fade=t=out:st={d - fout:.3f}:d={fout:.2f}:alpha=1"
        else:
            chain += f",fade=t=out:st={max(0.0, d - 0.6):.3f}:d=0.6:alpha=1"
        chain += f",setpts=PTS-STARTPTS+{u['start']:.3f}/TB[u{k}]"
        fl.append(chain)
        fl.append(f"[{cur}][u{k}]overlay={u['x']}:{u['y']}:eof_action=pass[o{k}]")
        cur = f"o{k}"
        idx += 1
    fl.append(f"[{cur}]trim=duration={duration:.3f},setpts=PTS-STARTPTS,format=yuv420p[vout]")
    args += ["-i", str(audio_wav)]
    aidx = idx
    filter_path = out_mp4.with_suffix(".filter.txt")
    filter_path.write_text(";\n".join(fl), encoding="utf-8")
    args += [
        "-filter_complex_script", str(filter_path),
        "-map", "[vout]", "-map", f"{aidx}:a",
        "-c:v", "libx264", "-preset", preset, "-crf", "18", "-profile:v", "high", "-level:v", "4.2",
        "-pix_fmt", "yuv420p", "-r", str(FPS), "-g", "60", "-maxrate", "8M", "-bufsize", "16M",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-t", f"{duration:.3f}", "-movflags", "+faststart",
        # clean file: no tool tags, no encoder banners, no metadata of any kind
        "-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact",
        "-flags:v", "+bitexact", "-flags:a", "+bitexact", "-bsf:v", "filter_units=remove_types=6",
        "-metadata:s:v", "handler_name=", "-metadata:s:a", "handler_name=", "-metadata:s:v", "encoder=",
        "-metadata:s:v", "language=und", "-metadata:s:a", "language=und",
        str(out_mp4),
    ]
    run(args)
    filter_path.unlink(missing_ok=True)
    return out_mp4
