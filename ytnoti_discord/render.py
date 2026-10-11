"""Turn an event config + context into a Discord webhook payload."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .config import DiscordCfg, EventCfg
from .templates import fmt, neutralize_mentions, truncate


def _iso(ctx: dict[str, str], which: str) -> str | None:
    if which == "none":
        return None
    if which == "now":
        return datetime.now(timezone.utc).isoformat()
    return ctx.get(which) or datetime.now(timezone.utc).isoformat()


def render_payload(ev: EventCfg, ctx: dict[str, str], discord: DiscordCfg) -> dict[str, Any]:
    payload: dict[str, Any] = {"allowed_mentions": discord.allowed_mentions}

    username = ev.username or discord.username
    avatar = ev.avatar_url or discord.avatar_url
    if username:
        payload["username"] = truncate(username, 80)
    if avatar:
        payload["avatar_url"] = avatar

    # Values substituted into message *content* are neutralised so a video
    # title can never ping @everyone or a role. Embeds never ping.
    safe_ctx = {k: neutralize_mentions(v) for k, v in ctx.items()}
    content = fmt(ev.content, safe_ctx).strip()
    if content:
        payload["content"] = truncate(content, 2000)

    if ev.embed is not None:
        e = ev.embed
        embed: dict[str, Any] = {"color": e.color}

        title = fmt(e.title, ctx).strip()
        if title:
            embed["title"] = truncate(title, 256)
        url = fmt(e.url, ctx).strip()
        if url and title:
            embed["url"] = url
        description = fmt(e.description, ctx).strip()
        if description:
            embed["description"] = truncate(description, 4096)
        footer = fmt(e.footer, ctx).strip()
        if footer:
            embed["footer"] = {"text": truncate(footer, 2048)}
        if e.author and ctx.get("channel_name"):
            author = {"name": truncate(ctx["channel_name"], 256)}
            if ctx.get("channel_url"):
                author["url"] = ctx["channel_url"]
            embed["author"] = author
        if ctx.get("thumbnail"):
            if e.image:
                embed["image"] = {"url": ctx["thumbnail"]}
            if e.thumbnail:
                embed["thumbnail"] = {"url": ctx["thumbnail"]}
        ts = _iso(ctx, e.timestamp)
        if ts:
            embed["timestamp"] = ts
        fields = []
        for f in e.fields[:25]:
            name, value = fmt(f.name, ctx).strip(), fmt(f.value, ctx).strip()
            if name and value:
                fields.append({"name": truncate(name, 256),
                               "value": truncate(value, 1024), "inline": f.inline})
        if fields:
            embed["fields"] = fields

        if len(embed) > 1:  # more than just a colour
            payload["embeds"] = [embed]

    return payload


def build_context(
    *,
    event: str,
    video_id: str,
    title: str,
    url: str,
    channel_id: str,
    channel_name: str,
    channel_url: str,
    old_title: str = "",
    change: str = "",
    thumbnail: str = "",
    published: datetime | None = None,
    updated: datetime | None = None,
    scheduled_ts: int | None = None,
    deleted_at: datetime | None = None,
) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    published = published or now
    updated = updated or now
    return {
        "event": event,
        "title": title,
        "old_title": old_title,
        "change": change,
        "video_id": video_id,
        "url": url,
        "thumbnail": thumbnail,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "channel_url": channel_url,
        "published": published.isoformat(),
        "updated": updated.isoformat(),
        "published_ts": str(int(published.timestamp())),
        "updated_ts": str(int(updated.timestamp())),
        "scheduled_ts": str(scheduled_ts or int(published.timestamp())),
        "deleted_ts": str(int((deleted_at or now).timestamp())),
    }
