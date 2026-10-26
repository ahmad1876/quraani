"""Post Reels to the Facebook Page directly with the Graph API (free, no Buffer channel needed).

Buffer Free has room for 3 channels (TikTok, Instagram, YouTube), and Instagram's automatic sharing
to Facebook skips posts made through Buffer, so the daily job schedules the Facebook Reels itself:
  1. start an upload session on the Page        POST /{page-id}/video_reels upload_phase=start
  2. let Facebook fetch the video from its link POST rupload.facebook.com/video-upload/{video-id}
  3. schedule it with the caption               POST /{page-id}/video_reels upload_phase=finish

Needs FACEBOOK_PAGE_TOKEN: a Page access token with pages_show_list, pages_read_engagement and
pages_manage_posts, made from a long-lived user token so it does not expire (see README). The Meta app
must be Live: posts made by an app in Development mode are only visible to the app's admins.
Docs: https://developers.facebook.com/docs/video-api/guides/reels-publishing
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "v26.0"
API = f"https://graph.facebook.com/{VERSION}"
RUPLOAD = f"https://rupload.facebook.com/video-upload/{VERSION}"
MAX_SEC = 90  # Facebook Reels are 3 to 90 seconds: the 3-minute Friday video is not posted there


class FacebookError(RuntimeError):
    pass


def token() -> str:
    return os.environ.get("FACEBOOK_PAGE_TOKEN", "").strip()


def _call(method: str, url: str, params: dict | None = None, headers: dict | None = None) -> dict:
    params = {k: v for k, v in (params or {}).items() if v is not None}
    data = None
    if method == "GET":
        url += ("?" + urllib.parse.urlencode(params)) if params else ""
    else:
        data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"User-Agent": "quraani-shorts/1.0", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read()).get("error", {})
        except Exception:
            err = {}
        msg = err.get("message", "") or str(e)
        if err.get("code") == 190:
            msg += " - the Page token has expired or was revoked: make a new one (README, Facebook setup)"
        raise FacebookError(f"HTTP {e.code}: {msg[:400]}")


def page() -> dict:
    """The Page the token belongs to: {'id', 'name', 'service': 'facebook'}."""
    if not token():
        raise FacebookError("FACEBOOK_PAGE_TOKEN is not set: add it under repo Settings > Secrets and variables > Actions")
    d = _call("GET", f"{API}/me", {"fields": "id,name", "access_token": token()})
    return {"id": d["id"], "name": d.get("name", ""), "service": "facebook"}


def schedule_reel(pg: dict, text: str, video_url: str, due_utc: dt.datetime) -> dict:
    """Upload the video from its public link and schedule it as a Reel. Returns {'id', 'due'}."""
    tok = token()
    start = _call("POST", f"{API}/{pg['id']}/video_reels", {"upload_phase": "start", "access_token": tok})
    vid = start.get("video_id")
    if not vid:
        raise FacebookError(f"no upload session: {str(start)[:300]}")
    up = _call("POST", f"{RUPLOAD}/{vid}", headers={"Authorization": f"OAuth {tok}", "file_url": video_url})
    if not up.get("success"):
        raise FacebookError(f"Facebook could not fetch the video: {str(up)[:300]}")
    fin = _call("POST", f"{API}/{pg['id']}/video_reels", {
        "upload_phase": "finish", "video_id": vid, "video_state": "SCHEDULED",
        "scheduled_publish_time": int(due_utc.timestamp()), "description": text, "access_token": tok})
    if not fin.get("success"):
        raise FacebookError(f"could not schedule the reel: {str(fin)[:300]}")
    _check_processing(vid, tok)
    return {"id": vid, "due": due_utc.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")}


def _check_processing(vid: str, tok: str, wait: int = 90) -> None:
    """Fail loudly if Facebook rejects the file; still processing after `wait` seconds is fine."""
    t0 = time.time()
    while time.time() - t0 < wait:
        st = _call("GET", f"{API}/{vid}", {"fields": "status", "access_token": tok}).get("status") or {}
        if st.get("video_status") in ("error", "upload_failed", "expired"):
            raise FacebookError(f"Facebook rejected the video: {json.dumps(st)[:300]}")
        proc = (st.get("processing_phase") or {}).get("status")
        if proc == "error":
            raise FacebookError(f"Facebook could not process the video: {json.dumps(st)[:300]}")
        if proc == "complete" or proc == "completed":
            return
        time.sleep(10)
