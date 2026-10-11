"""Discord webhook delivery with rate-limit handling.

Webhook URLs contain the secret token, so they are never logged; only the
user-chosen label is.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger("ytnoti_discord.discord")

MAX_RETRY_WAIT = 30.0


class DiscordSender:
    def __init__(self, client: httpx.AsyncClient, max_retries: int = 5) -> None:
        self._client = client
        self._max_retries = max_retries
        self._sleep = asyncio.sleep  # replaceable in tests

    async def send(self, label: str, url: str, payload: dict[str, Any]) -> bool:
        """POST a payload; returns True on success. Never raises."""
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = await self._client.post(url, json=payload)
            except httpx.TransportError as exc:
                log.warning("webhook %r: network error (%s), attempt %d/%d",
                            label, type(exc).__name__, attempt, self._max_retries)
                await self._sleep(min(2 ** attempt, MAX_RETRY_WAIT))
                continue

            status = resp.status_code
            if status in (200, 204):
                log.info("webhook %r: delivered", label)
                return True

            if status == 429:
                wait = _retry_after(resp)
                log.warning("webhook %r: rate limited, retrying in %.1fs", label, wait)
                await self._sleep(wait)
                continue

            if status >= 500:
                log.warning("webhook %r: Discord returned %d, attempt %d/%d",
                            label, status, attempt, self._max_retries)
                await self._sleep(min(2 ** attempt, MAX_RETRY_WAIT))
                continue

            # 4xx other than 429: retrying will not help.
            hint = {
                401: "invalid webhook token",
                404: "webhook was deleted or the URL is wrong",
                400: "Discord rejected the payload",
            }.get(status, "request failed")
            log.error("webhook %r: HTTP %d (%s): %s", label, status, hint,
                      resp.text[:300])
            return False

        log.error("webhook %r: giving up after %d attempts", label, self._max_retries)
        return False


def _retry_after(resp: httpx.Response) -> float:
    value: Any = None
    try:
        value = resp.json().get("retry_after")
    except (ValueError, AttributeError):
        pass
    if value is None:
        value = resp.headers.get("Retry-After")
    try:
        return max(0.1, min(float(value), MAX_RETRY_WAIT))
    except (TypeError, ValueError):
        return 1.0
