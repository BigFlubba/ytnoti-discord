"""Live-stream detection and thumbnails.

The ytnoti library only reports upload/edit/delete; it cannot tell a live
stream from a normal video. This module asks YouTube directly:

* With ``youtube_api_key`` set: the official Data API (videos.list, 1 quota unit).
* Without a key: parse the public watch page (no quota, but depends on YouTube's
  page format, so it is the less robust option).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import httpx

log = logging.getLogger("ytnoti_discord.youtube")

Status = Literal["live", "upcoming", "none"]

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_MARKER = "ytInitialPlayerResponse"


@dataclass(frozen=True)
class LiveInfo:
    status: Status
    scheduled_ts: int | None = None


def _iso_to_ts(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def parse_player_response(html: str) -> LiveInfo | None:
    """Extract live status from a watch page. None if the page is unrecognised."""
    idx = html.find(_MARKER)
    while idx != -1:
        brace = html.find("{", idx)
        eq = html.find("=", idx)
        if 0 <= eq < brace and brace - eq < 6:  # `ytInitialPlayerResponse = {`
            try:
                data, _ = json.JSONDecoder().raw_decode(html[brace:])
            except ValueError:
                return None
            break
        idx = html.find(_MARKER, idx + 1)
    else:
        return None

    details = data.get("videoDetails") or {}
    micro = (data.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    broadcast = micro.get("liveBroadcastDetails") or {}
    playability = (data.get("playabilityStatus") or {}).get("status")

    if details.get("isLive") or broadcast.get("isLiveNow"):
        return LiveInfo("live")
    if details.get("isUpcoming") or playability == "LIVE_STREAM_OFFLINE":
        return LiveInfo("upcoming", _iso_to_ts(broadcast.get("startTimestamp")))
    if details or micro:
        return LiveInfo("none")
    return None


def parse_api_item(item: dict[str, Any]) -> LiveInfo | None:
    state = (item.get("snippet") or {}).get("liveBroadcastContent")
    if state not in ("live", "upcoming", "none"):
        return None
    scheduled = (item.get("liveStreamingDetails") or {}).get("scheduledStartTime")
    return LiveInfo(state, _iso_to_ts(scheduled) if state == "upcoming" else None)


class YouTubeClient:
    def __init__(self, client: httpx.AsyncClient, api_key: str | None = None) -> None:
        self._client = client
        self._api_key = api_key

    async def live_info(self, video_id: str) -> LiveInfo | None:
        """Return the video's live state, or None if it could not be determined."""
        if self._api_key:
            info = await self._via_api(video_id)
            if info is not None:
                return info
            log.info("API lookup failed for %s, falling back to page scrape", video_id)
        return await self._via_page(video_id)

    async def _via_api(self, video_id: str) -> LiveInfo | None:
        try:
            resp = await self._client.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "snippet,liveStreamingDetails", "id": video_id,
                        "key": self._api_key},
            )
        except httpx.HTTPError as exc:
            log.warning("YouTube API request failed (%s)", type(exc).__name__)
            return None
        if resp.status_code != 200:
            try:
                reason = resp.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                reason = resp.text[:200]
            log.warning("YouTube API HTTP %d: %s", resp.status_code, reason)
            return None
        items = resp.json().get("items") or []
        return parse_api_item(items[0]) if items else None

    async def _via_page(self, video_id: str) -> LiveInfo | None:
        try:
            resp = await self._client.get(
                "https://www.youtube.com/watch",
                params={"v": video_id},
                headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9",
                         "Cookie": "CONSENT=YES+1; SOCS=CAI"},
            )
        except httpx.HTTPError as exc:
            log.warning("watch-page request failed (%s)", type(exc).__name__)
            return None
        info = parse_player_response(resp.text)
        if info is None:
            log.warning(
                "could not read live status for %s from the watch page "
                "(HTTP %d). Set 'youtube_api_key' for reliable live detection.",
                video_id, resp.status_code,
            )
        return info

    async def thumbnail_url(self, video_id: str) -> str:
        """Best available thumbnail: maxres when it exists, else hqdefault."""
        base = f"https://i.ytimg.com/vi/{video_id}"
        try:
            resp = await self._client.head(f"{base}/maxresdefault.jpg")
            if resp.status_code == 200:
                return f"{base}/maxresdefault.jpg"
        except httpx.HTTPError:
            pass
        return f"{base}/hqdefault.jpg"
