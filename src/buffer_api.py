"""Minimal client for Buffer's public GraphQL API (free plan includes 1 API key).

Docs: https://developers.buffer.com
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

API = "https://api.buffer.com"


class BufferError(RuntimeError):
    pass


def _key() -> str:
    k = os.environ.get("BUFFER_API_KEY", "").strip()
    if not k:
        raise BufferError("BUFFER_API_KEY is not set: add it under repo Settings > Secrets and variables > Actions")
    return k


def gql(query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "Authorization": f"Bearer {_key()}", "Content-Type": "application/json",
        "User-Agent": "quraani-shorts/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise BufferError(f"HTTP {e.code}: {e.read()[:500]!r}")
    if data.get("errors"):
        raise BufferError(json.dumps(data["errors"])[:800])
    return data["data"]


def channels() -> list[dict]:
    orgs = gql("query { account { organizations { id name } } }")["account"]["organizations"]
    out = []
    for o in orgs:
        q = "query($id: OrganizationId!) { channels(input: {organizationId: $id}) { id name service } }"
        for c in gql(q, {"id": o["id"]})["channels"]:
            out.append({**c, "organization": o["id"]})
    return out


def pick_channels(wanted=("tiktok", "instagram", "youtube")) -> dict[str, dict]:
    found = {}
    for c in channels():
        s = (c.get("service") or "").lower()
        if s in wanted and s not in found:
            found[s] = c
    return found


CREATE = """mutation($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id dueAt } }
    ... on MutationError { message }
  }
}"""


def schedule_video(channel: dict, text: str, video_url: str, due_at_utc: str, *,
                   thumb_ms: int = 1500, youtube_title: str = "") -> dict:
    service = channel["service"].lower()
    meta: dict = {}
    if service == "instagram":
        meta = {"instagram": {"type": "reel", "shouldShareToFeed": True, "isAiGenerated": False}}
    elif service == "tiktok":
        meta = {"tiktok": {"isAiGenerated": False}}
    elif service == "youtube":
        meta = {"youtube": {"title": youtube_title or text[:95], "categoryId": "22", "madeForKids": False,
                            "isAiGenerated": False, "notifySubscribers": True}}
    inp = {
        "channelId": channel["id"],
        "text": text,
        "schedulingType": "automatic",
        "mode": "customScheduled",
        "dueAt": due_at_utc,
        "assets": [{"video": {"url": video_url, "metadata": {"thumbnailOffset": thumb_ms}}}],
        "metadata": meta,
        "aiAssisted": False,
    }
    res = gql(CREATE, {"input": inp})["createPost"]
    if "post" not in res:
        raise BufferError(res.get("message", "createPost failed"))
    return res["post"]
