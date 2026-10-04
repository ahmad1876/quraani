"""Shared helpers: paths, cached HTTP, ffmpeg runner, config."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("QURAANI_CACHE", ROOT / ".cache"))
FONTS = ROOT / "fonts"
CATALOG = ROOT / "catalog"
STATE = ROOT / "state"

UA = "Mozilla/5.0 (X11; Linux x86_64) quraani-shorts/1.0"


def load_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def load_bundled_secrets() -> None:
    """Let all keys live in ONE GitHub secret (QURAANI_SECRETS) as KEY=VALUE lines."""
    blob = os.environ.get("QURAANI_SECRETS", "")
    for line in blob.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and not os.environ.get(k):
            os.environ[k] = v


load_bundled_secrets()


def config() -> dict:
    cfg = load_json(ROOT / "config.json", {}) or {}
    return cfg


def http_get(url: str, *, headers: dict | None = None, timeout: int = 60, retries: int = 4) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # network hiccups are common on free CDNs
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} tries: {url}: {last}")


def get_json(url: str, *, cache_days: float = 30, headers: dict | None = None):
    """GET JSON with an on-disk cache (API responses for Quran data never change)."""
    key = hashlib.sha1(url.encode()).hexdigest()
    p = CACHE / "http" / f"{key}.json"
    if p.exists() and (time.time() - p.stat().st_mtime) < cache_days * 86400:
        return json.loads(p.read_text(encoding="utf-8"))
    data = json.loads(http_get(url, headers=headers).decode("utf-8"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def download(url: str, dest: Path, *, retries: int = 4, timeout: int = 300) -> Path:
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f, length=1 << 20)
            tmp.replace(dest)
            return dest
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"download failed: {url}: {last}")


def url_basename(url: str) -> str:
    return Path(urllib.parse.urlparse(url).path).name or "file"


def run(cmd: list[str], *, quiet: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        tail = (res.stderr or "")[-3000:]
        raise RuntimeError(f"command failed ({res.returncode}): {' '.join(cmd[:6])} ...\n{tail}")
    return res


def ffprobe_duration(path: Path) -> float:
    res = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)])
    return float(res.stdout.strip())


def arabic_digits(n: int) -> str:
    return str(n).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
