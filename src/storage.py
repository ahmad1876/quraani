"""Public links for finished videos, so Buffer can fetch them.

Default: GitHub Pages of this (public) repo. Each run force-pushes a fresh
one-commit `gh-pages` branch holding only the videos that still have posts
waiting, so the repo never grows. Needs nothing but the workflow's own
GITHUB_TOKEN. Optional: Cloudflare R2 when R2_* variables are set.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

INDEX = """<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex">
<title>media</title><p>Media for scheduled posts.</p>"""


def backend() -> str:
    if os.environ.get("R2_BUCKET", "").strip():
        return "r2"
    return "github_pages"


# ------------------------------------------------------------ GitHub Pages --
def pages_base() -> str:
    repo = os.environ["GITHUB_REPOSITORY"]
    owner, name = repo.split("/", 1)
    if name.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io"
    return f"https://{owner.lower()}.github.io/{name}"


def _git(*args, cwd=None, check=True):
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {res.stderr[-800:]}")
    return res


def _remote() -> str:
    token = os.environ["GITHUB_TOKEN"]
    return f"https://x-access-token:{token}@github.com/{os.environ['GITHUB_REPOSITORY']}.git"


def publish_pages(files: list[str], keep: set[str]) -> dict[str, str]:
    """Push `files` (plus still-needed `keep` names) to gh-pages and wait until they are live."""
    work = Path(tempfile.mkdtemp(prefix="pages-"))
    have_branch = _git("clone", "--quiet", "--depth", "1", "--branch", "gh-pages", _remote(), str(work),
                       check=False).returncode == 0
    if not have_branch:
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        _git("init", "--quiet", cwd=work)
    vdir = work / "v"
    vdir.mkdir(exist_ok=True)
    for old in vdir.glob("*.mp4"):
        if old.name not in keep:
            old.unlink()
    for f in files:
        shutil.copy2(f, vdir / Path(f).name)
    (work / ".nojekyll").write_text("", encoding="utf-8")
    (work / "index.html").write_text(INDEX, encoding="utf-8")
    (work / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    _git("config", "user.name", "quraani-bot", cwd=work)
    _git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com", cwd=work)
    _git("checkout", "--quiet", "--orphan", "publish", cwd=work)
    _git("add", "-A", cwd=work)
    _git("commit", "--quiet", "-m", f"media {dt.date.today().isoformat()}", cwd=work)
    _git("push", "--quiet", "--force", _remote(), "HEAD:gh-pages", cwd=work)
    shutil.rmtree(work, ignore_errors=True)
    _request_build()
    base = pages_base()
    urls = {Path(f).name: f"{base}/v/{Path(f).name}" for f in files}
    for url in urls.values():
        wait_live(url)
    return urls


def _request_build() -> None:
    """Ask GitHub to rebuild Pages now (harmless if a build is already running)."""
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/pages/builds", method="POST",
            headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                     "Accept": "application/vnd.github+json"})
        urllib.request.urlopen(req, timeout=30).read()
    except Exception:
        pass


def is_live(url: str) -> bool:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "quraani"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except Exception:
        return False


def wait_live(url: str, timeout: int = 900) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "quraani"})
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(15)
    raise RuntimeError(f"{url} is not live after {timeout}s - is GitHub Pages enabled (Settings > Pages > "
                       f"Deploy from a branch > gh-pages)?")


def ensure_pages_branch() -> bool:
    """Create an empty gh-pages branch if missing (so Pages can be switched on). Returns True if created."""
    probe = _git("ls-remote", "--heads", _remote(), "gh-pages", check=False)
    if probe.stdout.strip():
        return False
    publish_pages_files_only([])
    return True


def publish_pages_files_only(files: list[str]) -> None:
    work = Path(tempfile.mkdtemp(prefix="pages-"))
    _git("init", "--quiet", cwd=work)
    (work / "v").mkdir()
    for f in files:
        shutil.copy2(f, work / "v" / Path(f).name)
    (work / ".nojekyll").write_text("", encoding="utf-8")
    (work / "index.html").write_text(INDEX, encoding="utf-8")
    (work / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    (work / "v" / "check.txt").write_text("ok", encoding="utf-8")
    _git("config", "user.name", "quraani-bot", cwd=work)
    _git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com", cwd=work)
    _git("checkout", "--quiet", "--orphan", "publish", cwd=work)
    _git("add", "-A", cwd=work)
    _git("commit", "--quiet", "-m", "init media branch", cwd=work)
    _git("push", "--quiet", "--force", _remote(), "HEAD:gh-pages", cwd=work)
    shutil.rmtree(work, ignore_errors=True)
    _request_build()


# ------------------------------------------------------------- R2 (optional) --
def _r2():
    import boto3
    from botocore.config import Config
    acct = os.environ["R2_ACCOUNT_ID"].strip()
    return boto3.client(
        "s3", endpoint_url=f"https://{acct}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"].strip(),
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"].strip(),
        region_name="auto", config=Config(signature_version="s3v4", retries={"max_attempts": 5}))


def publish_r2(files: list[str]) -> dict[str, str]:
    bucket = os.environ["R2_BUCKET"].strip()
    base = os.environ["R2_PUBLIC_BASE_URL"].strip().rstrip("/")
    s3 = _r2()
    out = {}
    for f in files:
        key = f"videos/{Path(f).name}"
        s3.upload_file(f, bucket, key, ExtraArgs={"ContentType": "video/mp4"})
        out[Path(f).name] = f"{base}/{key}"
    return out


# ------------------------------------------------------------------ facade --
def publish(files: list[str], keep: set[str]) -> dict[str, str]:
    return publish_r2(files) if backend() == "r2" else publish_pages(files, keep)


def pending_names(history: list[dict], now_utc: dt.datetime, grace_hours: int = 18) -> set[str]:
    """Video files that still have a post waiting (or posted very recently)."""
    keep = set()
    for h in history:
        url = h.get("url")
        if not url:
            continue
        dues = [p.get("due") for p in h.get("posts", {}).values() if p.get("due")]
        for d in dues:
            try:
                when = dt.datetime.fromisoformat(d.replace("Z", "+00:00"))
            except ValueError:
                continue
            if when > now_utc - dt.timedelta(hours=grace_hours):
                keep.add(url.rsplit("/", 1)[-1])
                break
    return keep


def describe() -> str:
    return json.dumps({"backend": backend()})
