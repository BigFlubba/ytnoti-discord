"""YAML configuration: loading, ${ENV} interpolation, validation, defaults."""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (BaseModel, ConfigDict, Field, ValidationError, field_validator,
                      model_validator)

from .templates import SAMPLE_CONTEXT, fmt

EVENT_NAMES = ("upload", "live", "upcoming", "edit", "delete")

CHANNEL_ID_RE = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
WEBHOOK_URL_RE = re.compile(
    r"^https://([a-z]+\.)?discord(app)?\.com/api(/v\d+)?/webhooks/\d+/[\w-]+/?(\?.*)?$"
)
ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


class ConfigError(Exception):
    """Raised for any problem with the configuration file."""


# --------------------------------------------------------------------------- #
# Built-in message defaults (everything here can be overridden in the YAML)
# --------------------------------------------------------------------------- #

BUILTIN_EVENTS: dict[str, dict[str, Any]] = {
    "upload": {
        "content": "\U0001F4FA **{channel_name}** just uploaded a new video!",
        "embed": {"title": "{title}", "url": "{url}", "color": 0xFF0000,
                  "timestamp": "published"},
    },
    "live": {
        "content": "\U0001F534 **{channel_name}** is live now!",
        "embed": {"title": "{title}", "url": "{url}", "color": 0xE62117,
                  "timestamp": "now"},
    },
    "upcoming": {
        "enabled": False,
        "content": "\U0001F4C5 **{channel_name}** scheduled a live stream.",
        "embed": {"title": "{title}", "url": "{url}",
                  "description": "Starts <t:{scheduled_ts}:R>", "color": 0x3498DB,
                  "timestamp": "now"},
    },
    "edit": {
        "cooldown_seconds": 300,
        "content": "",
        "embed": {"title": "\u270F\uFE0F Video updated: {title}", "url": "{url}",
                  "description": "{change}", "color": 0xFFA500, "image": False,
                  "thumbnail": True, "timestamp": "updated"},
    },
    "delete": {
        "content": "",
        "embed": {"title": "\U0001F5D1\uFE0F Video deleted", "url": "",
                  "description": "**{title}**\n`{video_id}`", "color": 0x808080,
                  "image": False, "timestamp": "now"},
    },
}


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Section(_Strict):
    """A config section: an empty/null value (e.g. all children commented out)
    means "use the defaults" rather than being an error."""

    @model_validator(mode="before")
    @classmethod
    def _drop_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
        return data


class FieldCfg(_Strict):
    name: str
    value: str
    inline: bool = False


class EmbedCfg(_Strict):
    title: str = "{title}"
    url: str = "{url}"
    description: str = ""
    color: int = 0xFF0000
    author: bool = True          # show the channel name as the embed author
    image: bool = True           # large thumbnail under the embed
    thumbnail: bool = False      # small thumbnail in the corner
    footer: str = ""
    timestamp: Literal["published", "updated", "now", "none"] = "now"
    fields: list[FieldCfg] = Field(default_factory=list)

    @field_validator("color", mode="before")
    @classmethod
    def _color(cls, v: Any) -> Any:
        if isinstance(v, str):
            s = v.strip().lstrip("#")
            if s.lower().startswith("0x"):
                s = s[2:]
            try:
                return int(s, 16)
            except ValueError as exc:
                raise ValueError(f"invalid color {v!r}; use e.g. '#FF0000'") from exc
        return v


class EventCfg(_Strict):
    enabled: bool = True
    webhooks: list[str] | None = None
    username: str | None = None
    avatar_url: str | None = None
    content: str = ""
    embed: EmbedCfg | None = Field(default_factory=EmbedCfg)
    # edit-only options
    cooldown_seconds: int = Field(default=0, ge=0)
    only_if_title_changed: bool = False
    # edit-only: show a before/after of the title, description and tags in {change}
    show_diff: bool = True

    @field_validator("embed", mode="before")
    @classmethod
    def _embed(cls, v: Any) -> Any:
        return None if v is False else v


class ServerCfg(_Section):
    callback_url: str
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    secret: str | None = None
    # ytnoti checks each channel ID with a HEAD request to youtube.com and treats
    # any non-200 as "invalid". Set false if that check misfires on your network.
    verify_channels: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Validate the config, print the summary and exit instead of running, exactly
    # like `--check` -- but set from the file, so no container flag is needed.
    # While true the service will not start: set it back to false to run normally.
    check_only: bool = False
    # Send sample notifications at every start, with no CLI flag needed:
    # false (default) | true (all five events) | a list such as [upload, live].
    # Handy for checking routing after a deploy; turn it off afterwards, since
    # every restart posts the samples again.
    test_on_start: bool | list[str] = False
    # Optional rotating log file. A relative path is resolved against data_dir.
    log_file: str | None = None
    log_max_bytes: int = Field(default=5_000_000, ge=10_000)
    log_backup_count: int = Field(default=3, ge=0, le=100)

    @field_validator("test_on_start", mode="before")
    @classmethod
    def _test_on_start(cls, v: Any) -> Any:
        if isinstance(v, str):  # e.g. from ${TEST_ON_START:-false} or "upload,live"
            text = v.strip().lower()
            if text in ("", "false", "0", "no", "off"):
                return False
            if text in ("true", "1", "yes", "on", "all"):
                return True
            v = [part.strip() for part in text.split(",") if part.strip()]
        if isinstance(v, list):
            bad = [e for e in v if e not in EVENT_NAMES]
            if bad:
                raise ValueError(f"unknown event(s) {bad}; valid: {', '.join(EVENT_NAMES)}")
            return list(dict.fromkeys(v)) or False   # de-duplicate; [] means off
        return v

    @field_validator("log_file", mode="before")
    @classmethod
    def _empty_log_file(cls, v: Any) -> Any:
        return v or None

    @field_validator("callback_url")
    @classmethod
    def _url(cls, v: str) -> str:
        if not re.match(r"^https?://[^/\s]+", v):
            raise ValueError("must be a full URL such as https://yt.example.com/ytnoti")
        return v

    @field_validator("secret", mode="before")
    @classmethod
    def _empty_secret(cls, v: Any) -> Any:
        return v or None


class DiscordCfg(_Section):
    username: str | None = None
    avatar_url: str | None = None
    # Safe default: roles/users may be pinged, @everyone/@here may not.
    allowed_mentions: dict[str, Any] = Field(
        default_factory=lambda: {"parse": ["roles", "users"]}
    )
    max_retries: int = Field(default=5, ge=1, le=10)


class DefaultsCfg(_Section):
    webhooks: list[str] | None = None
    events: dict[str, dict[str, Any] | None] = Field(default_factory=dict)


class ChannelCfg(_Section):
    id: str
    name: str | None = None
    webhooks: list[str] | None = None
    events: dict[str, dict[str, Any] | None] = Field(default_factory=dict)


class RootCfg(_Section):
    server: ServerCfg
    data_dir: Path = Path("/data")
    youtube_api_key: str | None = None
    discord: DiscordCfg = Field(default_factory=DiscordCfg)
    webhooks: dict[str, str]
    defaults: DefaultsCfg = Field(default_factory=DefaultsCfg)
    channels: list[ChannelCfg]

    @field_validator("youtube_api_key", mode="before")
    @classmethod
    def _empty_key(cls, v: Any) -> Any:
        return v or None


# --------------------------------------------------------------------------- #
# Resolved runtime objects
# --------------------------------------------------------------------------- #

@dataclass
class Channel:
    id: str
    name: str | None
    events: dict[str, EventCfg]
    # event name -> [(label, url)]; labels are safe to log, URLs are secrets.
    # Empty for disabled events.
    targets: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    # Same routing, but also filled for disabled events, so `--test` can send a
    # sample of every event type. Never used for real notifications.
    test_targets: dict[str, list[tuple[str, str]]] = field(default_factory=dict)


@dataclass
class Config:
    server: ServerCfg
    data_dir: Path
    youtube_api_key: str | None
    discord: DiscordCfg
    channels: dict[str, Channel]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def interpolate(obj: Any) -> Any:
    """Expand ${VAR} and ${VAR:-default} in every string of the loaded YAML."""
    if isinstance(obj, str):
        def sub(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            value = os.environ.get(name)
            if value:
                return value
            if default is not None:
                return default
            raise ConfigError(f"environment variable {name!r} is not set")
        return ENV_RE.sub(sub, obj)
    if isinstance(obj, dict):
        return {k: interpolate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [interpolate(v) for v in obj]
    return obj


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


def _templates_of(ev: EventCfg) -> list[tuple[str, str]]:
    items = [("content", ev.content)]
    if ev.embed:
        e = ev.embed
        items += [("embed.title", e.title), ("embed.url", e.url),
                  ("embed.description", e.description), ("embed.footer", e.footer)]
        for i, f in enumerate(e.fields):
            items += [(f"embed.fields[{i}].name", f.name),
                      (f"embed.fields[{i}].value", f.value)]
    return items


def _resolve_webhooks(
    entries: list[str], named: dict[str, str], where: str
) -> list[tuple[str, str]]:
    out = []
    for entry in entries:
        if entry in named:
            out.append((entry, named[entry]))
        elif WEBHOOK_URL_RE.match(entry):
            out.append(("inline", entry))
        else:
            raise ConfigError(
                f"{where}: unknown webhook {entry!r} "
                f"(defined webhooks: {', '.join(named) or 'none'})"
            )
    return out


# --------------------------------------------------------------------------- #
# Loader
# --------------------------------------------------------------------------- #

def load_config(path: str | os.PathLike[str]) -> Config:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config file {p}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{p} is not valid YAML:\n{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{p} must contain a YAML mapping at the top level")

    raw = interpolate(raw)
    try:
        root = RootCfg.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration:\n{_format_errors(exc)}") from exc

    # Webhook URLs
    if not root.webhooks:
        raise ConfigError("'webhooks' must define at least one Discord webhook")
    for name, url in root.webhooks.items():
        if not WEBHOOK_URL_RE.match(url):
            raise ConfigError(
                f"webhooks.{name}: not a Discord webhook URL "
                "(expected https://discord.com/api/webhooks/<id>/<token>)"
            )

    for scope, events in [("defaults.events", root.defaults.events)]:
        for name in events:
            if name not in EVENT_NAMES:
                raise ConfigError(f"{scope}: unknown event {name!r} "
                                  f"(valid: {', '.join(EVENT_NAMES)})")

    if not root.channels:
        raise ConfigError("'channels' must list at least one YouTube channel")

    channels: dict[str, Channel] = {}
    for ch in root.channels:
        where = f"channels[{ch.id}]"
        if not CHANNEL_ID_RE.match(ch.id):
            raise ConfigError(
                f"{where}: {ch.id!r} is not a channel ID. IDs look like "
                "'UCxxxxxxxxxxxxxxxxxxxxxx' (24 chars). @handles are not accepted."
            )
        if ch.id in channels:
            raise ConfigError(f"{where}: channel listed twice")
        for name in ch.events:
            if name not in EVENT_NAMES:
                raise ConfigError(f"{where}.events: unknown event {name!r} "
                                  f"(valid: {', '.join(EVENT_NAMES)})")

        base_hooks = ch.webhooks or root.defaults.webhooks
        if base_hooks is None and len(root.webhooks) == 1:
            base_hooks = list(root.webhooks)

        events: dict[str, EventCfg] = {}
        targets: dict[str, list[tuple[str, str]]] = {}
        test_targets: dict[str, list[tuple[str, str]]] = {}
        for name in EVENT_NAMES:
            merged = deep_merge(BUILTIN_EVENTS[name], (root.defaults.events.get(name) or {}))
            merged = deep_merge(merged, (ch.events.get(name) or {}))
            try:
                ev = EventCfg.model_validate(merged)
            except ValidationError as exc:
                raise ConfigError(
                    f"invalid settings for {where}.events.{name}:\n{_format_errors(exc)}"
                ) from exc

            for label, template in _templates_of(ev):
                try:
                    fmt(template, SAMPLE_CONTEXT)
                except (KeyError, IndexError, ValueError, AttributeError) as exc:
                    raise ConfigError(
                        f"{where}.events.{name}.{label}: bad template {template!r} "
                        f"({type(exc).__name__}: {exc}). Use only the documented "
                        "{variables}; write literal braces as {{ and }}."
                    ) from exc

            events[name] = ev
            hooks = ev.webhooks if ev.webhooks is not None else base_hooks
            if ev.enabled:
                if not hooks:
                    raise ConfigError(
                        f"{where}.events.{name}: no webhook configured. Set "
                        "'webhooks' on the channel, event, or under 'defaults'."
                    )
                targets[name] = _resolve_webhooks(
                    hooks, root.webhooks, f"{where}.events.{name}"
                )
                test_targets[name] = targets[name]
            else:
                targets[name] = []
                # Lenient: a disabled event's routing was never validated before,
                # so a problem here must not break configs that used to load.
                try:
                    test_targets[name] = _resolve_webhooks(
                        hooks or [], root.webhooks, f"{where}.events.{name}")
                except ConfigError:
                    test_targets[name] = []

        channels[ch.id] = Channel(id=ch.id, name=ch.name, events=events,
                                  targets=targets, test_targets=test_targets)

    return Config(
        server=root.server,
        data_dir=root.data_dir,
        youtube_api_key=root.youtube_api_key,
        discord=root.discord,
        channels=channels,
    )
