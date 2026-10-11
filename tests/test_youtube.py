import json

import httpx
import pytest

from ytnoti_discord.youtube import (LiveInfo, YouTubeClient, parse_api_item,
                                    parse_player_response)


def page(player: dict, extra_marker: bool = True) -> str:
    junk = 'var x = {"ytInitialPlayerResponseConfig": 1};' if extra_marker else ""
    return f"<html><script>{junk}var ytInitialPlayerResponse = {json.dumps(player)};var y=1;</script></html>"


def test_live_page():
    assert parse_player_response(page({"videoDetails": {"isLive": True}})) == LiveInfo("live")


def test_live_via_microformat():
    p = {"videoDetails": {}, "microformat": {"playerMicroformatRenderer":
         {"liveBroadcastDetails": {"isLiveNow": True}}}}
    assert parse_player_response(page(p)).status == "live"


def test_upcoming_page_with_schedule():
    p = {"videoDetails": {"isUpcoming": True},
         "microformat": {"playerMicroformatRenderer": {"liveBroadcastDetails":
                         {"startTimestamp": "2026-10-12T18:00:00+00:00"}}}}
    info = parse_player_response(page(p))
    assert info.status == "upcoming" and info.scheduled_ts == 1791828000


def test_upcoming_via_playability():
    assert parse_player_response(page({"playabilityStatus": {"status": "LIVE_STREAM_OFFLINE"},
                                       "videoDetails": {}})).status == "upcoming"


def test_normal_and_ended_stream():
    assert parse_player_response(page({"videoDetails": {"videoId": "a"}})).status == "none"
    ended = {"videoDetails": {"isLiveContent": True}, "microformat": {"playerMicroformatRenderer":
             {"liveBroadcastDetails": {"isLiveNow": False}}}}
    assert parse_player_response(page(ended)).status == "none"


@pytest.mark.parametrize("html", ["", "<html>consent</html>",
                                  "var ytInitialPlayerResponse = {broken"])
def test_unrecognised_pages_return_none(html):
    assert parse_player_response(html) is None


def test_api_item():
    assert parse_api_item({"snippet": {"liveBroadcastContent": "live"}}).status == "live"
    up = parse_api_item({"snippet": {"liveBroadcastContent": "upcoming"},
                         "liveStreamingDetails": {"scheduledStartTime": "2026-10-12T18:00:00Z"}})
    assert up.status == "upcoming" and up.scheduled_ts == 1791828000
    assert parse_api_item({"snippet": {}}) is None


def test_client_prefers_api_then_falls_back_to_page():
    import asyncio

    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.host)
        if req.url.host == "www.googleapis.com":
            return httpx.Response(403, json={"error": {"message": "quota"}})
        return httpx.Response(200, text=page({"videoDetails": {"isLive": True}}))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await YouTubeClient(c, "KEY").live_info("abc")

    assert asyncio.run(go()).status == "live"
    assert calls == ["www.googleapis.com", "www.youtube.com"]
