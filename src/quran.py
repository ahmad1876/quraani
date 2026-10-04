"""Quran text + translation from the public quran.com API (cached on disk)."""
from __future__ import annotations

import re
from functools import lru_cache

from common import arabic_digits, get_json

API = "https://api.quran.com/api/v4"
SAHEEH = 20  # Saheeh International
WAQF_MARKS = set("ۖۗۘۙۚۛۜ")
# Stronger stops first: ۗ (qala), ۚ (jeem), ۖ (sala), ۛ/ۙ weaker
STRONG_STOP = set("ۗۚۖۛ")
DROP_CHARS = dict.fromkeys(map(ord, "۞۩‏‎﻿"), None)


@lru_cache(maxsize=None)
def chapters() -> dict[int, dict]:
    data = get_json(f"{API}/chapters?language=en")
    return {c["id"]: c for c in data["chapters"]}


def chapter(n: int) -> dict:
    c = chapters()[n]
    return {
        "id": n,
        "name_en": c["name_simple"],
        "name_ar": c["name_arabic"],
        "meaning": c.get("translated_name", {}).get("name", ""),
        "verses": c["verses_count"],
    }


def clean_ar(t: str) -> str:
    t = t.translate(DROP_CHARS)
    return re.sub(r"\s+", " ", t).strip()


def clean_translation(t: str) -> str:
    t = re.sub(r"<sup[^>]*>.*?</sup>", "", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "", t)
    t = t.replace("—", " - ").replace("–", "-").replace("’", "'").replace("‘", "'")
    t = t.replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip()


@lru_cache(maxsize=None)
def verse(key: str) -> dict:
    """Return {'key','text','words':[{'pos','text','waqf'}],'translation'} for 's:a'."""
    data = get_json(
        f"{API}/verses/by_key/{key}?words=true&word_fields=text_uthmani"
        f"&fields=text_uthmani&translations={SAHEEH}"
    )["verse"]
    words = []
    for w in data["words"]:
        if w.get("char_type_name") != "word":
            continue
        txt = clean_ar(w["text_uthmani"])
        if not txt:
            continue
        marks = [c for c in txt if c in WAQF_MARKS]
        words.append({"pos": w["position"], "text": txt, "waqf": marks[-1] if marks else ""})
    tr = ""
    if data.get("translations"):
        tr = clean_translation(data["translations"][0]["text"])
    return {
        "key": key,
        "text": clean_ar(data["text_uthmani"]),
        "words": words,
        "translation": tr,
    }


def ayah_marker(n: int) -> str:
    """End-of-ayah ornament with the number (rendered by Amiri Quran)."""
    return "۝" + arabic_digits(n)


def letters(t: str) -> int:
    """Count base Arabic letters (ignores diacritics/marks) to estimate reading time and width."""
    return sum(1 for c in t if "ء" <= c <= "ي" or c in "ٱیہ")
