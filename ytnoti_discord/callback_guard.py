"""Hardening for the public WebSub callback endpoint.

The ``ytnoti`` library serves the callback URL itself. Its handlers trust the
request more than a public endpoint should: any ``hub.challenge`` is echoed back
for any topic, a malformed ``hub.lease_seconds`` or ``X-Hub-Signature`` raises
(HTTP 500 plus a traceback in the logs), and request bodies are unbounded.

This ASGI middleware sits in front of the library's routes, for the callback
path only, and:

* answers hub verification (GET/HEAD) only for configured channels' topics, with
  strictly validated parameters, and always as ``text/plain`` + ``nosniff``;
* rejects pushes (POST) with an oversized body (413) or a malformed signature
  header (401) before the library sees them;
* turns unexpected handler errors into empty 4xx/5xx responses instead of leaking
  tracebacks or request content.

Signature *verification* stays with the library (constant-time HMAC compare).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs

log = logging.getLogger("ytnoti_discord.callback")

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

TOPIC_PREFIX = "https://www.youtube.com/xml/feeds/videos.xml?channel_id="
MAX_BODY_BYTES = 1024 * 1024        # real feed pushes are a few KB
MAX_CHALLENGE_LEN = 512
MAX_LEASE_DIGITS = 9                # < ~31 years; keeps timedelta() safe

_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9._~+/=:-]+$")   # opaque token: no markup
_SIGNATURE_RE = re.compile(r"^(sha1|sha256|sha384|sha512)=[0-9a-fA-F]{40,128}$")
_PARSE_ERRORS = (RuntimeError, ValueError, TypeError, KeyError)

_BASE_HEADERS = [
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
]


def topic_for(channel_id: str) -> str:
    return TOPIC_PREFIX + channel_id


class CallbackGuard:
    def __init__(self, app: Any, *, path: str, topics: set[str],
                 max_body: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.path = path
        self.topics = frozenset(topics)
        self.max_body = max_body

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != self.path:
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        if method in ("GET", "HEAD"):
            await self._verification(scope, receive, send)
        elif method == "POST":
            await self._push(scope, receive, send)
        else:
            await self._respond(send, 405, headers=[(b"allow", b"GET, HEAD, POST")])

    # -- hub verification / denial -------------------------------------------

    async def _verification(self, scope: Scope, receive: Receive, send: Send) -> None:
        raw = scope.get("query_string", b"").decode("latin-1")
        qs = parse_qs(raw, keep_blank_values=True)

        # The library requests its own callback URL at startup and needs a 200.
        if "startup_test" in qs:
            await self._forward(scope, receive, send)
            return

        def one(key: str) -> str | None:
            values = qs.get(key)
            return values[0] if values and len(values) == 1 else None

        mode, topic = one("hub.mode"), one("hub.topic")
        if topic is None or topic not in self.topics:
            # Includes unknown topics: the spec's answer for "I never asked for that".
            await self._respond(send, 404)
            return

        if mode == "denied":
            log.warning("hub denied the subscription for %s (reason: %s)", topic,
                        (one("hub.reason") or "none given")[:200])
            await self._respond(send, 200, b"OK")
            return

        challenge = one("hub.challenge")
        if (mode not in ("subscribe", "unsubscribe") or challenge is None
                or len(challenge) > MAX_CHALLENGE_LEN
                or not _CHALLENGE_RE.match(challenge)):
            await self._respond(send, 400)
            return

        lease = qs.get("hub.lease_seconds")
        if lease is not None and not (
                len(lease) == 1 and lease[0].isascii() and lease[0].isdigit()
                and len(lease[0]) <= MAX_LEASE_DIGITS):
            await self._respond(send, 400)
            return

        await self._forward(scope, receive, send)

    # -- pushes -----------------------------------------------------------------

    async def _push(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = {k.lower(): v for k, v in scope.get("headers", [])}

        # Cheap checks first, before reading any body.
        signature = headers.get(b"x-hub-signature", b"").decode("latin-1")
        if not _SIGNATURE_RE.match(signature):
            await self._respond(send, 401)
            return
        declared = headers.get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.max_body):
            await self._respond(send, 413)
            return

        # Read the body ourselves so chunked uploads are capped too, then replay it.
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.max_body:
                await self._respond(send, 413)
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks),
                        "more_body": False}
            return await receive()

        await self._forward(scope, replay, send, on_error_status=True)

    # -- plumbing ---------------------------------------------------------------

    async def _forward(self, scope: Scope, receive: Receive, send: Send,
                       *, on_error_status: bool = False) -> None:
        started = False

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                headers += [h for h in _BASE_HEADERS if h[0] not in present]
                if message.get("status") == 200 and b"content-type" not in present:
                    headers.append((b"content-type", b"text/plain; charset=utf-8"))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, guarded_send)
        except Exception as exc:
            # Log the type only: messages from the library can embed the request body.
            log.warning("callback handler failed (%s)", type(exc).__name__)
            if not started:
                # Unparseable pushes will never succeed, so don't invite hub retries;
                # anything else may be transient, so let the hub retry.
                status = 400 if (on_error_status and isinstance(exc, _PARSE_ERRORS)) else 500
                await self._respond(send, status)

    @staticmethod
    async def _respond(send: Send, status: int, body: bytes = b"",
                       headers: list[tuple[bytes, bytes]] | None = None) -> None:
        out = list(_BASE_HEADERS) + [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
        ] + (headers or [])
        await send({"type": "http.response.start", "status": status, "headers": out})
        await send({"type": "http.response.body", "body": body})
