"""The callback guard in front of the real ytnoti routes (no network)."""
import asyncio
import hashlib
import hmac
import socket
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI
from ytnoti import AsyncYouTubeNotifier

from ytnoti_discord.callback_guard import CallbackGuard, topic_for
from conftest import CHANNEL

SECRET = "s3cret"
PATH = "/ytnoti"
TOPIC = topic_for(CHANNEL)
FEED = (b"<?xml version='1.0'?><feed xmlns:yt='http://www.youtube.com/xml/schemas/2015' "
        b"xmlns='http://www.w3.org/2005/Atom'><entry><yt:videoId>V</yt:videoId>"
        b"<yt:channelId>" + CHANNEL.encode() + b"</yt:channelId><title>t</title>"
        b"<link rel='alternate' href='https://www.youtube.com/watch?v=V'/>"
        b"<author><name>n</name><uri>https://www.youtube.com/channel/x</uri></author>"
        b"<published>2024-01-01T00:00:00+00:00</published>"
        b"<updated>2024-01-01T00:00:00+00:00</updated></entry></feed>")


def sign(body: bytes, algo: str = "sha1") -> dict[str, str]:
    return {"X-Hub-Signature": f"{algo}=" + hmac.new(SECRET.encode(), body, algo).hexdigest()}


def make_app(max_body: int = 1024 * 1024, base: str = "https://yt.example.com",
             routes: bool = True):
    app = FastAPI()
    notifier = AsyncYouTubeNotifier(callback_url=f"{base}{PATH}", password=SECRET, app=app)
    if routes:  # a real server registers them itself in run()
        notifier._set_app_routes(app=app, callback_url=f"{base}{PATH}")
    notifier._subscribed_ids.add(CHANNEL)
    app.add_middleware(CallbackGuard, path=PATH, topics={TOPIC}, max_body=max_body)
    return app, notifier


def call(method: str, url: str = PATH, *, app=None, **kw) -> httpx.Response:
    app = app or make_app()[0]

    async def go():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.request(method, url, **kw)
    return asyncio.run(go())


def verify(**params):
    q = {"hub.mode": "subscribe", "hub.topic": TOPIC, "hub.challenge": "abc123",
         "hub.lease_seconds": "432000"}
    q.update(params)
    return call("GET", PATH, params={k: v for k, v in q.items() if v is not None})


# -- hub verification ----------------------------------------------------------

def test_valid_verification_is_echoed_as_plain_text():
    r = verify()
    assert r.status_code == 200 and r.text == "abc123"
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"


def test_unknown_topic_is_not_echoed():
    other = topic_for("UC" + "x" * 22)
    r = verify(**{"hub.topic": other})
    assert r.status_code == 404 and "abc123" not in r.text


def test_challenge_cannot_carry_markup_or_be_huge():
    assert verify(**{"hub.challenge": "<script>alert(1)</script>"}).status_code == 400
    assert verify(**{"hub.challenge": "a" * 600}).status_code == 400
    assert verify(**{"hub.challenge": None}).status_code == 400


def test_bad_mode_and_lease_values_are_400_not_500():
    assert verify(**{"hub.mode": "evil"}).status_code == 400
    assert verify(**{"hub.lease_seconds": "abc"}).status_code == 400
    assert verify(**{"hub.lease_seconds": "9" * 40}).status_code == 400


def test_duplicate_parameters_are_rejected():
    r = call("GET", f"{PATH}?hub.mode=subscribe&hub.topic={TOPIC}"
                    "&hub.challenge=a&hub.challenge=b")
    assert r.status_code == 400


def test_denied_subscription_is_acknowledged(caplog):
    r = call("GET", PATH, params={"hub.mode": "denied", "hub.topic": TOPIC,
                                  "hub.reason": "nope"})
    assert r.status_code == 200
    assert "denied" in caplog.text


def test_startup_probe_still_works():
    r = call("HEAD", PATH, params={"startup_test": "1"})
    assert r.status_code == 200
    assert call("GET", PATH, params={"startup_test": "1"}).text == "OK"


def test_other_methods_and_paths_untouched():
    assert call("PUT", PATH).status_code == 405
    assert call("GET", "/elsewhere").status_code == 404


# -- pushes ----------------------------------------------------------------------

def test_signed_push_is_accepted():
    app, notifier = make_app()
    notifier._video_history = AsyncMock(has=AsyncMock(return_value=False))
    r = call("POST", PATH, app=app, content=FEED, headers=sign(FEED))
    assert r.status_code == 204


def test_missing_or_malformed_signature_is_401_never_500():
    assert call("POST", PATH, content=FEED).status_code == 401
    for bad in ("sha1", "sha1=zz", "md5=" + "0" * 32, "sha1=a=b", "x" * 5000,
                "sha1=" + "0" * 39):
        r = call("POST", PATH, content=FEED, headers={"X-Hub-Signature": bad})
        assert r.status_code == 401, bad


def test_wrong_signature_is_401():
    headers = {"X-Hub-Signature": "sha1=" + "0" * 40}
    assert call("POST", PATH, content=FEED, headers=headers).status_code == 401


def test_oversized_body_is_413():
    app, _ = make_app(max_body=100)
    body = b"x" * 101
    assert call("POST", PATH, app=app, content=body, headers=sign(body)).status_code == 413


def test_oversized_chunked_body_is_413():
    app, _ = make_app(max_body=100)

    async def chunks():
        for _ in range(5):
            yield b"x" * 50

    r = call("POST", PATH, app=app, content=chunks(), headers=sign(b"x" * 250))
    assert r.status_code == 413


def test_signed_garbage_is_400_without_leaking_content(caplog):
    body = b"<not-closed>" + b"SENSITIVE" * 3
    r = call("POST", PATH, content=body, headers=sign(body))
    assert r.status_code == 400 and r.text == ""
    assert "SENSITIVE" not in caplog.text


def test_signed_xml_missing_fields_is_400():
    body = b"<feed><entry><title>only a title</title></entry></feed>"
    assert call("POST", PATH, content=body, headers=sign(body)).status_code == 400


def test_transient_failure_is_500_so_the_hub_retries():
    app, notifier = make_app()
    notifier._video_history = AsyncMock(has=AsyncMock(side_effect=OSError("disk")))
    r = call("POST", PATH, app=app, content=FEED, headers=sign(FEED))
    assert r.status_code == 500 and r.text == ""


# -- through a real uvicorn server ------------------------------------------------

def test_real_server_hides_server_header_and_caps_body():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app, notifier = make_app(max_body=100, base=f"http://127.0.0.1:{port}", routes=False)

    async def scenario():
        notifier._request = AsyncMock()
        async with notifier.run_in_background(host="127.0.0.1", port=port,
                                              log_config=None, server_header=False):
            async with httpx.AsyncClient() as c:
                base = f"http://127.0.0.1:{port}{PATH}"
                ok = await c.get(base, params={"hub.mode": "subscribe",
                                               "hub.topic": TOPIC, "hub.challenge": "z9"})
                big = b"x" * 500
                bad = await c.post(base, content=big, headers=sign(big))
            return ok, bad

    ok, bad = asyncio.run(scenario())
    assert ok.status_code == 200 and ok.text == "z9" and "server" not in ok.headers
    assert bad.status_code == 413
