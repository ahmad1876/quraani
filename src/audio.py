"""Audio fetching, boundary refinement, pause detection and loudness normalisation."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
import wave
from pathlib import Path

import numpy as np

from common import CACHE, UA, download, load_json, run, save_json

SR = 48000
HOP = 0.02  # envelope hop in seconds


def _probe_cache() -> dict:
    return load_json(CACHE / "cbr_probe.json", {}) or {}


def is_cbr(url: str) -> bool:
    """Constant-bitrate MP3s can be seeked remotely with frame accuracy; VBR cannot."""
    cache = _probe_cache()
    if url in cache:
        return cache[url]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-393215"})
        with urllib.request.urlopen(req, timeout=60) as r:
            d = r.read()
        off = 0
        if d[:3] == b"ID3":
            off = 10 + ((d[6] & 0x7F) << 21 | (d[7] & 0x7F) << 14 | (d[8] & 0x7F) << 7 | (d[9] & 0x7F))
        if d.find(b"Xing", off, off + 400) > 0 or d.find(b"VBRI", off, off + 400) > 0:
            ok = False
        else:
            brs = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
            srs = [44100, 48000, 32000]
            seen, k, n = set(), off, 0
            while k < len(d) - 4 and n < 300:
                if d[k] == 0xFF and (d[k + 1] & 0xE6) == 0xE2:
                    bi, si, pad = (d[k + 2] >> 4) & 0xF, (d[k + 2] >> 2) & 3, (d[k + 2] >> 1) & 1
                    if 0 < bi < 15 and si < 3:
                        seen.add(brs[bi])
                        k += 144 * brs[bi] * 1000 // srs[si] + pad
                        n += 1
                        continue
                k += 1
            ok = len(seen) == 1 and n > 50
    except Exception:
        ok = False
    cache[url] = ok
    save_json(CACHE / "cbr_probe.json", cache)
    return ok


def extract(url: str, t0: float, t1: float, out_wav: Path) -> Path:
    """Decode [t0, t1] seconds of a remote MP3 to 48 kHz stereo WAV."""
    t0 = max(0.0, t0)
    src = url
    if not is_cbr(url):
        name = hashlib.sha1(url.encode()).hexdigest()[:16] + ".mp3"
        src = str(download(url, CACHE / "audio" / name))
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.3f}", "-i", src, "-t", f"{t1 - t0:.3f}",
         "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(out_wav)])
    return out_wav


def read_mono(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        ch = w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    if ch > 1:
        x = x.reshape(-1, ch).mean(1)
    return x


def envelope(x: np.ndarray, sr: int = SR) -> np.ndarray:
    n = int(sr * HOP)
    m = len(x) // n
    if m == 0:
        return np.array([-90.0])
    e = np.sqrt((x[: m * n].reshape(m, n) ** 2).mean(1) + 1e-12)
    return 20 * np.log10(e)


def quietest(env: np.ndarray, t: float, window: float) -> float:
    """Time (s) of the quietest 100 ms inside t +/- window."""
    i0 = max(0, int((t - window) / HOP))
    i1 = min(len(env), int((t + window) / HOP) + 1)
    if i1 - i0 < 3:
        return t
    seg = np.convolve(env[i0:i1], np.ones(5) / 5, mode="same")
    return (i0 + int(np.argmin(seg))) * HOP


def pauses(env: np.ndarray, t_from: float, t_to: float, min_len: float = 0.16) -> list[tuple[float, float]]:
    """Breath pauses between t_from and t_to: runs well below the speech level."""
    i0, i1 = max(0, int(t_from / HOP)), min(len(env), int(t_to / HOP))
    if i1 - i0 < 10:
        return []
    seg = env[i0:i1]
    speech = np.percentile(seg, 75)
    floor = np.percentile(env, 5)
    thr = max(floor + 6, speech - 16)
    quiet = seg < thr
    out, start = [], None
    for k, q in enumerate(list(quiet) + [False]):
        if q and start is None:
            start = k
        elif not q and start is not None:
            if (k - start) * HOP >= min_len:
                out.append(((i0 + start) * HOP, (i0 + k) * HOP))
            start = None
    return out


def finalize(raw_wav: Path, cut0: float, cut1: float, out_wav: Path, target_lufs: float = -14.0,
             tail: float = 0.4) -> float:
    """Trim, fade and loudness-normalise (two-pass EBU R128). Returns duration."""
    dur = cut1 - cut0
    fade_out = min(0.3, dur / 8)
    base = (f"atrim=start={cut0:.3f}:end={cut1:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d=0.04,afade=t=out:st={dur - fade_out:.3f}:d={fade_out:.3f}")
    probe = run(["ffmpeg", "-v", "info", "-hide_banner", "-i", str(raw_wav), "-af",
                 base + f",loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", probe.stderr, re.S)
    if m:
        j = json.loads(m.group(0))
        ln = (f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:measured_I={j['input_i']}:measured_TP={j['input_tp']}:"
              f"measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    else:
        ln = f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(raw_wav), "-af",
         base + "," + ln + f",aresample={SR},apad=pad_dur={tail:.2f}",
         "-ac", "2", "-c:a", "pcm_s16le", str(out_wav)])
    return dur + tail
