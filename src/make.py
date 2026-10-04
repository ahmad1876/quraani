"""Build one finished short: pick span, cut audio, time the text, add footage, render."""
from __future__ import annotations

import shutil
from pathlib import Path

import audio
import compose
import footage
import overlay
import quran
import recitation
import segments
from common import ffprobe_duration

MAX_SEC = 44.6   # hard ceiling is 45 s
MIN_SEC = 9.0
TAIL = 0.4      # calm silence after the last ayah


KEEP_CAPS = {"He", "Him", "His", "Allah", "Allāh", "I", "We", "Our", "Us", "You", "Your", "Lord", "Moses", "Jesus",
             "Mary", "Abraham", "Joseph", "Noah", "Jacob", "John", "Zechariah", "Muhammad", "Muḥammad", "Paradise",
             "Hell", "Qur'an", "Qur'ān", "Gabriel", "The"}


def join_translation(parts: list[str]) -> str:
    """Join verse translations so a sentence running across verses reads naturally."""
    out = ""
    for t in parts:
        t = t.strip()
        if out and out[-1] not in '.!?"' and t[:1].isupper():
            first = t.split(" ", 1)[0].strip(",;:")
            if first not in KEEP_CAPS:
                t = t[0].lower() + t[1:]
        out = f"{out} {t}".strip()
    return out


def plan_span(passage: dict, reciter_key: str):
    t = recitation.timing(reciter_key, passage["surah"])
    return t, recitation.fit_span(t, passage["start"], passage["end"], MAX_SEC - 0.9, MIN_SEC,
                                  passage.get("need", 0))


def make_video(passage: dict, reciter_key: str, out_mp4: Path, *, history: list | None = None,
               handle: str = "", seed: int = 0, work: Path | None = None, preset: str = "medium",
               local_footage: str | None = None) -> dict:
    history = history or []
    rec = recitation.reciters()[reciter_key]
    ch = passage["surah"]
    t, span = plan_span(passage, reciter_key)
    if not span:
        raise ValueError(f"{passage['id']} does not fit {MAX_SEC}s for {reciter_key}")
    a, b, t0, t1 = span
    vs = t["verses"]
    out_mp4 = Path(out_mp4).resolve()
    work = Path(work or out_mp4.with_suffix("")).resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    # 1) audio: decode a little extra around the span, then cut on silence
    r0 = max(0.0, t0 - 0.9)
    nxt = vs.get(b + 1)
    r1 = t1 + 1.0
    raw = audio.extract(t["audio_url"], r0, r1, work / "raw.wav")
    env = audio.envelope(audio.read_mono(raw))
    if t["kind"] == "word":
        # quran.com chapter files are spliced per ayah: cut on the quietest point near the edges
        cut0 = r0 + audio.quietest(env, max(0.0, t0 - r0 - 0.15), 0.15)
        hi = (recitation._first_sound(nxt) + 0.05) if nxt else t1 + 0.6
        mid = min(t1 + 0.1, hi - 0.05)
        cut1 = r0 + audio.quietest(env, mid - r0, min(0.3, max(0.05, hi - mid)))
        cut1 = max(cut1, t1 - 0.05)
    else:
        cut0 = r0 + audio.quietest(env, t0 - r0, 0.35)
        cut1 = r0 + audio.quietest(env, t1 - r0, 0.35)
    cut1 = min(cut1, cut0 + MAX_SEC - TAIL)
    final_wav = work / "audio.wav"
    dur = audio.finalize(raw, cut0 - r0, cut1 - r0, final_wav, tail=TAIL)

    # 2) on-screen phrases synced to the reciter
    units = segments.build_units(ch, a, b, t, env, r0, cut0, cut1, total=dur)

    # 3) real nature footage, no people or animals
    n = max(1, min(4, round(dur / 12.5)))
    clips = footage.pick_clips(n, dur / n + 1.2, history, seed=seed, local_dir=local_footage)

    # 4) text overlays + final render
    chap = quran.chapter(ch)
    ov = overlay.render(units, "سورة " + chap["name_ar"], rec["name_ar"], work / "ov", handle=handle)
    compose.build(clips, final_wav, ov, out_mp4, dur, seed=seed, preset=preset)
    shutil.rmtree(work, ignore_errors=True)

    ref = f"{ch}:{a}" if a == b else f"{ch}:{a}-{b}"
    return {
        "file": str(out_mp4),
        "passage": passage["id"],
        "ref": ref,
        "surah": ch,
        "ayah_from": a,
        "ayah_to": b,
        "surah_en": chap["name_en"],
        "surah_ar": chap["name_ar"],
        "surah_meaning": chap["meaning"],
        "reciter": reciter_key,
        "reciter_en": rec["name_en"],
        "reciter_ar": rec["name_ar"],
        "duration": round(ffprobe_duration(out_mp4), 2),
        "clips": [c["id"] for c in clips],
        "footage_theme": clips[0].get("theme", "") if clips else "",
        "credits": sorted({c["credit"] for c in clips if c.get("credit")}),
        "hook": passage.get("hook", ""),
        "theme": passage.get("theme", ""),
        "translation": join_translation([quran.verse(f"{ch}:{k}")["translation"] for k in range(a, b + 1)]),
    }
