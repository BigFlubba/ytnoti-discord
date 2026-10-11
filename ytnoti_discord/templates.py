"""Message-template helpers shared by config validation and rendering."""

from __future__ import annotations

# Every variable available inside {braces} in message templates.
CONTEXT_KEYS = (
    "event",
    "title",
    "old_title",
    "change",
    "video_id",
    "url",
    "thumbnail",
    "channel_id",
    "channel_name",
    "channel_url",
    "published",
    "updated",
    "published_ts",
    "updated_ts",
    "scheduled_ts",
    "deleted_ts",
)

SAMPLE_CONTEXT = {key: "x" for key in CONTEXT_KEYS}


def fmt(template: str, ctx: dict[str, str]) -> str:
    """Render a ``str.format`` template. Raises KeyError for unknown variables."""
    return template.format_map(ctx)


def neutralize_mentions(text: str) -> str:
    """Break @everyone / <@&role> / <@user> pings inside untrusted values."""
    return text.replace("@", "@\u200b")


def truncate(text: str, limit: int) -> str:
    """Truncate to Discord's field limits, ending with an ellipsis."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "\u2026"
