"""Minimal client for the YouTube Data API v3 (free key, no card, 10,000 units a day).

Used for two things:
  - the views, likes and comments of our own Shorts (stats.py), since Buffer's free plan
    has no post stats: videos.list costs 1 unit per 50 videos
  - what is popular right now among Quran Shorts (trends.py): search.list costs 100 units

Docs: https://developers.google.com/youtube/v3
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://www.googleapis.com/youtube/v3"
ID_RE = re.compile(r"(?:youtube\.com/(?:shorts/|watch\?(?:.*&)?v=|embed/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})")


class YouTubeError(RuntimeError):
    pass


def key() -> str:
    return os.environ.get("YOUTUBE_API_KEY", "").strip()


def get(path: str, **params) -> dict:
    k = key()
    if not k:
        raise YouTubeError("YOUTUBE_API_KEY is not set: add it under repo Settings > Secrets and variables > Actions")
    q = urllib.parse.urlencode({**{p: v for p, v in params.items() if v is not None}, "key": k})
    req = urllib.request.Request(f"{API}/{path}?{q}", headers={"User-Agent": "quraani-shorts/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        raise YouTubeError(f"HTTP {e.code}: {msg[:300]}")


def video_id(link: str | None) -> str | None:
    m = ID_RE.search(link or "")
    return m.group(1) if m else None


def stats(ids: list[str]) -> dict[str, dict]:
    """{video_id: {'views', 'likes', 'comments', 'title', 'published', 'channel'}} for public videos."""
    out: dict[str, dict] = {}
    ids = list(dict.fromkeys(i for i in ids if i))
    for i in range(0, len(ids), 50):
        data = get("videos", part="statistics,snippet", id=",".join(ids[i:i + 50]), maxResults=50)
        for v in data.get("items") or []:
            s, sn = v.get("statistics") or {}, v.get("snippet") or {}
            out[v["id"]] = {
                "views": int(s.get("viewCount", 0)),
                "likes": int(s["likeCount"]) if s.get("likeCount") is not None else None,
                "comments": int(s["commentCount"]) if s.get("commentCount") is not None else None,
                "title": sn.get("title", ""), "description": sn.get("description", ""),
                "published": sn.get("publishedAt"), "channel": sn.get("channelTitle", ""),
            }
    return out


def uploads(channel: str, limit: int = 150) -> list[tuple[str, dt.datetime]]:
    """[(video_id, published)] of a channel's latest uploads. `channel` is a @handle or a UC... id."""
    channel = channel.strip()
    if channel.startswith("UC"):
        data = get("channels", part="contentDetails", id=channel)
    else:
        data = get("channels", part="contentDetails", forHandle=channel if channel.startswith("@") else "@" + channel)
    items = data.get("items") or []
    if not items:
        raise YouTubeError(f"YouTube channel not found: {channel}")
    playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    out, page = [], None
    while len(out) < limit:
        data = get("playlistItems", part="contentDetails", playlistId=playlist, maxResults=50, pageToken=page)
        for it in data.get("items") or []:
            cd = it.get("contentDetails") or {}
            when = cd.get("videoPublishedAt")
            if cd.get("videoId") and when:
                out.append((cd["videoId"], dt.datetime.fromisoformat(when.replace("Z", "+00:00"))))
        page = data.get("nextPageToken")
        if not page:
            break
    return out


def search_shorts(query: str, published_after: dt.datetime, max_results: int = 50) -> list[str]:
    """Video ids of the most viewed short videos for `query` published since `published_after` (100 units)."""
    data = get("search", part="id", q=query, type="video", videoDuration="short", order="viewCount",
               publishedAfter=published_after.strftime("%Y-%m-%dT%H:%M:%SZ"), maxResults=max_results)
    return [it["id"]["videoId"] for it in data.get("items") or [] if it.get("id", {}).get("videoId")]
