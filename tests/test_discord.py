import asyncio

import httpx

from ytnoti_discord.discord_client import DiscordSender


def run_send(responses):
    seq = iter(responses)
    sleeps = []

    def handler(req):
        r = next(seq)
        if isinstance(r, Exception):
            raise r
        return r

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            s = DiscordSender(c, max_retries=4)

            async def fake_sleep(t):
                sleeps.append(t)
            s._sleep = fake_sleep
            return await s.send("main", "https://discord.com/api/webhooks/1/x", {"content": "hi"})

    return asyncio.run(go()), sleeps


def test_success():
    ok, sleeps = run_send([httpx.Response(204)])
    assert ok and sleeps == []


def test_429_honours_retry_after_then_succeeds():
    ok, sleeps = run_send([httpx.Response(429, json={"retry_after": 1.5}), httpx.Response(204)])
    assert ok and sleeps == [1.5]


def test_5xx_and_network_errors_are_retried():
    ok, sleeps = run_send([httpx.Response(502), httpx.ConnectError("boom"), httpx.Response(200)])
    assert ok and len(sleeps) == 2


def test_4xx_is_not_retried_and_does_not_leak_url(caplog):
    ok, sleeps = run_send([httpx.Response(404, text="Unknown Webhook")])
    assert not ok and sleeps == []
    assert "discord.com/api/webhooks" not in caplog.text


def test_gives_up_after_max_retries():
    ok, sleeps = run_send([httpx.Response(500)] * 4)
    assert not ok and len(sleeps) == 4
