"""Post captions and hashtags (plain ASCII punctuation, no hidden characters)."""
from __future__ import annotations

import re
import unicodedata

THEME_TAGS = {
    "dua": "dua", "sabr": "sabr", "jannah": "jannah", "mercy": "mercy", "hope": "hope",
    "akhirah": "akhirah", "tawakkul": "tawakkul", "parents": "parents", "kahf": "surahkahf",
    "yasin": "surahyasin", "mulk": "surahmulk", "rahman": "surahrahman", "protection": "protection",
    "healing": "healing", "shukr": "gratitude", "dhikr": "dhikr", "tawbah": "tawbah",
    "istighfar": "istighfar", "reminder": "islamicreminders", "tawheed": "tawheed",
    "names": "asmaulhusna", "salah": "salah", "qiyam": "tahajjud", "maryam": "surahmaryam",
    "taha": "surahtaha", "fatiha": "surahfatiha", "peace": "peace", "trust": "trustallah",
    "grief": "sabr", "marriage": "marriage", "character": "akhlaq", "guidance": "guidance",
    "purpose": "purpose", "dunya": "dunya", "rizq": "rizq", "ikhlas": "ikhlas", "reflection": "reflection",
    "salawat": "salawat", "qadr": "laylatulqadr", "tasbih": "tasbih", "nasr": "surahnasr",
}

BASE_TIKTOK = ["quran", "quranrecitation", "islam"]
BASE_IG = ["quran", "quranrecitation", "islam", "muslim", "allah", "quranverses", "islamicreminders", "tilawah"]
ARABIC_TAGS = ["قرآن", "تلاوة"]


def plain(text: str) -> str:
    """ASCII-friendly English: straight quotes, no dashes or invisible characters."""
    rep = {"—": " - ", "–": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
           "…": "...", "\u00a0": " "}
    for k, v in rep.items():
        text = text.replace(k, v)
    text = "".join(c for c in text if unicodedata.category(c) not in ("Cf", "Co", "Cs"))
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[ \t]+", " ", text).strip()


def surah_tag(name_en: str) -> str:
    n = re.sub(r"^(Al|Ar|As|At|An|Az|Ad|Ash|Adh|Ath|Ad-Dh)-", "", name_en, flags=re.I)
    n = re.sub(r"[^A-Za-z]", "", n).lower()
    return "surah" + n


def reciter_hashtag(meta: dict, tag: str) -> str:
    return re.sub(r"[^a-z0-9]", "", tag.lower())


def _short(text: str, limit: int) -> str:
    text = plain(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    m = max(cut.rfind(". "), cut.rfind("; "), cut.rfind(", "))
    cut = cut[: m + 1] if m > limit * 0.5 else cut.rsplit(" ", 1)[0]
    return cut.rstrip(",;") + " ..."


def follow_line(platform: str, handle: str = "") -> str:
    """One short call to follow, in each platform's own words."""
    if platform == "youtube":
        return "Subscribe for a calm ayah every day."
    h = handle.strip()
    if h and not h.startswith("@"):
        h = "@" + h
    return f"Follow {h} for a calm ayah every day." if h else "Follow for a calm ayah every day."


def build(meta: dict, platform: str, reciter_tag: str, credit: bool = True, handle: str = "") -> str:
    ref = meta["ref"]
    follow = follow_line(platform, handle)
    surah = plain(meta["surah_en"])
    hook = plain(meta.get("hook") or "")
    tr = _short(meta.get("translation", ""), 420 if platform in ("instagram", "youtube") else 300).replace('"', "'")
    if tr and not tr.endswith("..."):  # a verse cut mid-sentence ends with a dash or comma in the translation
        tr = re.sub(r"[\s,;:-]+$", "", tr)
        if tr and tr[-1] not in ".!?')]":
            tr += " ..."
    tags_extra = [reciter_hashtag(meta, reciter_tag), surah_tag(meta["surah_en"])]
    if meta["passage"] == "2:255":
        tags_extra.append("ayatulkursi")
    theme = THEME_TAGS.get(meta.get("theme", ""))
    if theme:
        tags_extra.append(theme)
    seen, tags = set(), []
    base = BASE_IG if platform == "instagram" else BASE_TIKTOK
    for t in base + tags_extra + (ARABIC_TAGS if platform == "instagram" else ARABIC_TAGS[:1]):
        if t and t not in seen:
            seen.add(t)
            tags.append("#" + t)
    names = [{"pexels": "Pexels", "mixkit": "Mixkit"}[s] for s in meta.get("sources", [])]
    footage = ("Footage: " + " / ".join(names)) if credit and names else ""
    if platform == "instagram":
        lines = [hook, "", f'"{tr}"', f"Surah {surah} {ref}", f"Recited by {plain(meta['reciter_en'])}", "",
                 "Save this and share it with someone who needs to hear it today.", follow, ""]
        if footage:
            lines += [footage, ""]
        lines.append(" ".join(tags))
    elif platform == "youtube":
        lines = [hook, "", f'"{tr}"', f"Surah {surah} {ref} - {plain(meta['reciter_en'])}", "", follow, ""]
        if footage:
            lines += [footage, ""]
        lines.append(" ".join(["#shorts"] + tags[:6]))
    else:  # tiktok
        lines = [hook, "", f'"{tr}"', f"Surah {surah} {ref} | {plain(meta['reciter_en'])}", "", follow]
        if footage:
            lines += [footage]
        lines.append(" ".join(tags[:7]))
    return "\n".join(lines).strip()


def youtube_title(meta: dict) -> str:
    """YouTube allows 100 characters: drop the reciter before cutting anything else."""
    hook = plain(meta["hook"]).rstrip(".")
    surah = f"Surah {plain(meta['surah_en'])} {meta['ref']}"
    for title in (f"{hook} | {surah} | {plain(meta['reciter_en'])}", f"{hook} | {surah}"):
        if len(title) <= 100:
            return title
    return hook[:100].rsplit(" ", 1)[0]
