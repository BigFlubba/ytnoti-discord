"""End to end: real ytnoti server + real HTTP POSTs with valid HMAC signatures.

Only the outbound calls (YouTube hub subscription, live lookup, Discord) are faked.
"""
import asyncio
import hashlib
import hmac
import socket
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI
from ytnoti import AsyncYouTubeNotifier

from ytnoti_discord.config import load_config
from ytnoti_discord.engine import Engine
from ytnoti_discord.state import StateStore
from ytnoti_discord.youtube import LiveInfo
from conftest import BASE, CHANNEL

SECRET = "s3cret"
VIDEO = "VIDEOID0001"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def entry_xml(title, published, updated):
    return f"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
<title>YouTube video feed</title><updated>{updated}</updated>
<entry><id>yt:video:{VIDEO}</id><yt:videoId>{VIDEO}</yt:videoId><yt:channelId>{CHANNEL}</yt:channelId>
<title>{title}</title><link rel="alternate" href="https://www.youtube.com/watch?v={VIDEO}"/>
<author><name>CBC News</name><uri>https://www.youtube.com/channel/{CHANNEL}</uri></author>
<published>{published}</published><updated>{updated}</updated></entry></feed>""".encode()


def deleted_xml(when):
    return f"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:at="http://purl.org/atompub/tombstones/1.0" xmlns="http://www.w3.org/2005/Atom">
<at:deleted-entry ref="yt:video:{VIDEO}" when="{when}">
<link href="https://www.youtube.com/watch?v={VIDEO}"/>
<at:by><name>CBC News</name><uri>https://www.youtube.com/channel/{CHANNEL}</uri></at:by>
</at:deleted-entry></feed>""".encode()


def test_full_pipeline(tmp_path, write_cfg):
    cfg = load_config(write_cfg(BASE + "\ndefaults:\n  events:\n    edit: {cooldown_seconds: 0}\n"))
    sent = []

    class Sender:
        async def send(self, label, url, payload):
            sent.append(payload)
            return True

    class YT:
        info = LiveInfo("none")

        async def live_info(self, vid):
            return self.info

        async def thumbnail_url(self, vid):
            return "https://i.ytimg.com/vi/x/hqdefault.jpg"

    yt = YT()
    engine = Engine(cfg, Sender(), yt, StateStore(tmp_path / "state.json"))
    port = free_port()
    now = datetime.now(timezone.utc)

    async def scenario():
        notifier = AsyncYouTubeNotifier(callback_url=f"http://127.0.0.1:{port}/ytnoti",
                                        password=SECRET, app=FastAPI())
        notifier._request = AsyncMock()              # no real hub subscription
        notifier._verify_channel_ids = AsyncMock()   # no real youtube.com lookup
        notifier.add_upload_listener(engine.on_upload)
        notifier.add_edit_listener(engine.on_edit)
        notifier.add_delete_listener(engine.on_delete)
        await notifier.subscribe(CHANNEL)

        async def push(body, signed=True):
            sig = hmac.new(SECRET.encode(), body, hashlib.sha1).hexdigest()
            headers = {"X-Hub-Signature": f"sha1={sig}"} if signed else {}
            async with httpx.AsyncClient() as c:
                r = await c.post(f"http://127.0.0.1:{port}/ytnoti", content=body, headers=headers)
            await engine.drain()
            return r.status_code

        async with notifier.run_in_background(host="127.0.0.1", port=port):
            t0 = now.isoformat()
            # forged push is rejected and produces nothing
            assert await push(entry_xml("Fake", t0, t0), signed=False) == 401
            assert sent == []

            # 1. new upload
            assert await push(entry_xml("My video", t0, t0)) == 204
            # 2. title edit
            t1 = datetime.now(timezone.utc).isoformat()
            assert await push(entry_xml("My video (renamed)", t0, t1)) == 204
            # 3. stream goes live
            yt.info = LiveInfo("live")
            t2 = datetime.now(timezone.utc).isoformat()
            assert await push(entry_xml("My video (renamed)", t0, t2)) == 204
            # 4. delete
            assert await push(deleted_xml(datetime.now(timezone.utc).isoformat())) == 204

    asyncio.run(scenario())

    kinds = [(p.get("content") or "", p["embeds"][0]["title"]) for p in sent]
    assert len(sent) == 4, kinds
    assert "uploaded" in kinds[0][0] and kinds[0][1] == "My video"
    assert "Video updated" in kinds[1][1]
    assert "Title changed" in sent[1]["embeds"][0]["description"]
    assert "live now" in kinds[2][0]
    assert "deleted" in kinds[3][1]
    assert "My video (renamed)" in sent[3]["embeds"][0]["description"]
