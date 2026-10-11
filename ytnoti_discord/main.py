"""Entrypoint: run the notifier, validate the config, or send test messages."""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import re
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Response
from ytnoti import AsyncYouTubeNotifier
from ytnoti.models.history import FileVideoHistory

from . import __version__
from .callback_guard import CallbackGuard, topic_for
from .diffing import describe_changes
from .discord_client import DiscordSender
from .engine import Engine
from .render import build_context, render_payload
from .state import StateStore
from .youtube import YouTubeClient

log = logging.getLogger("ytnoti_discord")


class _Throttle(logging.Filter):
    """The library retries its startup self-test every 0.5s; log it sparsely."""

    def __init__(self, every: float = 30.0) -> None:
        super().__init__()
        self._every, self._last = every, 0.0

    def filter(self, record: logging.LogRecord) -> bool:
        if "Callback URL test failed" not in record.getMessage():
            return True
        now = time.monotonic()
        if now - self._last >= self._every:
            self._last = now
            return True
        return False


_SECRET_PATTERNS = (
    (re.compile(r"(discord(?:app)?\.com/api(?:/v\d+)?/webhooks/)[^\s'\"]+", re.I),
     r"\1<redacted>"),
    (re.compile(r"([?&](?:key|token)=)[^&\s'\"]+", re.I), r"\1<redacted>"),
)


class _Redact(logging.Filter):
    """Last line of defence: never let a webhook URL or API key reach a log."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = message
        for pattern, repl in _SECRET_PATTERNS:
            redacted = pattern.sub(repl, redacted)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


def setup_logging(level: str, log_file: Path | None = None, *,
                  max_bytes: int = 5_000_000, backup_count: int = 3) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.handlers.RotatingFileHandler(
                log_file, maxBytes=max_bytes, backupCount=backup_count,
                encoding="utf-8"))
        except OSError as exc:  # e.g. read-only or unwritable volume
            print(f"warning: cannot write log file {log_file} ({exc}); "
                  "logging to the console only", file=sys.stderr)
    for handler in handlers:
        handler.setFormatter(fmt)
        handler.addFilter(_Redact())

    root = logging.getLogger()
    for old in root.handlers[:]:  # replace, so repeated calls don't duplicate output
        root.removeHandler(old)
        old.close()
    root.setLevel(level)
    for handler in handlers:
        root.addHandler(handler)

    # httpx logs every request URL at INFO -- that would print Discord webhook
    # tokens and the YouTube API key. Keep these quiet.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    throttled = logging.getLogger("AsyncYouTubeNotifier")
    if not any(isinstance(f, _Throttle) for f in throttled.filters):
        throttled.addFilter(_Throttle())


def resolve_log_file(cfg: Config) -> Path | None:
    if not cfg.server.log_file:
        return None
    path = Path(cfg.server.log_file)
    return path if path.is_absolute() else cfg.data_dir / path


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(15.0), follow_redirects=True,
        headers={"User-Agent": f"ytnoti-discord/{__version__}"},
    )


# --------------------------------------------------------------------------- #
# --check / --test
# --------------------------------------------------------------------------- #

def print_summary(cfg: Config) -> None:
    print(f"Callback URL : {cfg.server.callback_url}")
    print(f"Listening on : {cfg.server.host}:{cfg.server.port}")
    if cfg.server.test_on_start:
        which = "all events" if cfg.server.test_on_start is True else \
            ", ".join(cfg.server.test_on_start)
        print(f"Test on start: {which} (first channel; turn off after verifying)")
    if cfg.server.log_file:
        print(f"Log file     : {resolve_log_file(cfg)}")
    print(f"Live detect  : {'YouTube Data API' if cfg.youtube_api_key else 'watch-page scrape (no API key)'}")
    print(f"Channels     : {len(cfg.channels)}")
    for ch in cfg.channels.values():
        enabled = [e for e in EVENT_NAMES if ch.events[e].enabled]
        hooks = sorted({label for e in enabled for label, _ in ch.targets[e]})
        print(f"  - {ch.id} {ch.name or ''}".rstrip())
        print(f"      events : {', '.join(enabled) or 'none'}")
        print(f"      sends to: {', '.join(hooks) or 'none'}")


SAMPLE_VIDEO_ID = "jNQXAC9IVRw"  # the first video ever uploaded to YouTube


def build_test_context(channel: Channel, event: str,
                       now: datetime | None = None) -> dict[str, str]:
    """Sample data for one event type, shaped like what the engine produces."""
    now = now or datetime.now(timezone.utc)
    kwargs: dict[str, object] = {}
    title = f"[TEST] Sample {event} notification"
    if event == "edit":
        kwargs.update(
            old_title="[TEST] Old title",
            change=describe_changes(
                old_title="[TEST] Old title", new_title="[TEST] New title",
                old_description="Welcome!\nOld sponsor line\nFollow us for more",
                new_description="Welcome!\nNew sponsor line\nFollow us for more\nChapters below",
                old_tags=["test", "old-tag"], new_tags=["test", "new-tag"]))
    elif event == "upcoming":
        kwargs["scheduled_ts"] = int((now + timedelta(hours=1)).timestamp())
    elif event == "delete":
        kwargs["deleted_at"] = now
    return build_context(
        event=event, video_id=SAMPLE_VIDEO_ID, title=title,
        url=f"https://www.youtube.com/watch?v={SAMPLE_VIDEO_ID}",
        channel_id=channel.id, channel_name=channel.name or "Test Channel",
        channel_url=f"https://www.youtube.com/channel/{channel.id}",
        thumbnail=f"https://i.ytimg.com/vi/{SAMPLE_VIDEO_ID}/hqdefault.jpg",
        published=now, updated=now, **kwargs,  # type: ignore[arg-type]
    )


def mark_as_test(payload: dict[str, object], event: str) -> dict[str, object]:
    """Label the message as a test (even for embed-less events) and disable pings."""
    tag = f"\U0001F9EA **[TEST: {event}]**"
    content = str(payload.get("content") or "")
    payload["content"] = f"{tag} {content}".strip()[:2000]
    payload["allowed_mentions"] = {"parse": []}  # a test must never ping anyone
    return payload


async def send_test_notifications(
    cfg: Config, events: list[str], channel_id: str | None,
    client: httpx.AsyncClient,
) -> list[tuple[str, str]]:
    """Send a sample of each event; returns [(event, 'sent'|'failed'|'skipped')]."""
    channel = cfg.channels[channel_id] if channel_id else next(iter(cfg.channels.values()))
    sender = DiscordSender(client, cfg.discord.max_retries)
    results: list[tuple[str, str]] = []
    for event in events or list(EVENT_NAMES):
        targets = channel.test_targets.get(event) or []
        if not targets:
            results.append((event, "skipped"))
            continue
        ctx = build_test_context(channel, event)
        payload = mark_as_test(render_payload(channel.events[event], ctx, cfg.discord),
                               event)
        ok = all([await sender.send(label, url, payload) for label, url in targets])
        results.append((event, "sent" if ok else "failed"))
    return results


async def run_test(cfg: Config, events: list[str], channel_id: str | None) -> int:
    """CLI `--test`: sample of each requested event (default: ALL five, even if disabled)."""
    if channel_id and channel_id not in cfg.channels:
        print(f"error: channel {channel_id} is not in the config", file=sys.stderr)
        return 2
    channel = cfg.channels[channel_id] if channel_id else next(iter(cfg.channels.values()))

    async with _http_client() as client:
        results = await send_test_notifications(cfg, events, channel_id, client)
    failures = 0
    for event, status in results:
        if status == "skipped":
            print(f"- {event}: skipped (no webhook configured)")
            continue
        note = "" if channel.events[event].enabled else \
            " (event is disabled in config; sent as a test only)"
        print(f"- {event}: {'sent' if status == 'sent' else 'FAILED'}{note}")
        failures += status == "failed"
    return 1 if failures else 0


async def _startup_test(cfg: Config, client: httpx.AsyncClient) -> None:
    """`server.test_on_start`: runs in the background so it never delays startup."""
    events = [] if cfg.server.test_on_start is True else list(cfg.server.test_on_start)
    channel = next(iter(cfg.channels.values()))
    log.info("test_on_start: sending sample notifications (%s) for channel %s",
             ", ".join(events) or "all events", channel.id)
    try:
        results = await send_test_notifications(cfg, events, None, client)
    except Exception:  # a failed test must never take the service down
        log.exception("test_on_start failed")
        return
    for event, status in results:
        (log.info if status == "sent" else log.warning)("test_on_start: %s %s", event, status)


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #

async def serve(cfg: Config) -> None:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    state = StateStore(cfg.data_dir / "state.json")
    history = FileVideoHistory(dir_path=cfg.data_dir / "history", num_videos=200)

    notifier = AsyncYouTubeNotifier(
        callback_url=cfg.server.callback_url,
        password=cfg.server.secret,
        video_history=history,
        app=app,
    )

    app.add_middleware(
        CallbackGuard,
        path=urlparse(cfg.server.callback_url).path or "/",
        topics={topic_for(channel_id) for channel_id in cfg.channels},
    )

    @app.get("/healthz")
    async def healthz() -> Response:
        # Healthy only once the callback URL is reachable and subscriptions ran.
        return Response("ok" if notifier.is_ready else "starting",
                        status_code=200 if notifier.is_ready else 503,
                        media_type="text/plain")

    async with _http_client() as client:
        engine = Engine(cfg, DiscordSender(client, cfg.discord.max_retries),
                        YouTubeClient(client, cfg.youtube_api_key), state)
        notifier.add_upload_listener(engine.on_upload)
        notifier.add_edit_listener(engine.on_edit)
        notifier.add_delete_listener(engine.on_delete)

        if not cfg.server.verify_channels:
            async def _skip_verification(_ids: object) -> None:
                return None
            notifier._verify_channel_ids = _skip_verification  # type: ignore[method-assign]

        subscribed = 0
        for channel_id in cfg.channels:
            try:
                await notifier.subscribe(channel_id)
                subscribed += 1
            except ValueError as exc:
                log.error("skipping channel: %s", exc)
            except Exception as exc:  # network trouble while verifying the ID
                log.error("could not verify channel %s (%s: %s); skipping",
                          channel_id, type(exc).__name__, exc)
        if not subscribed:
            raise SystemExit("no valid channels to subscribe to; exiting")

        # uvicorn restores the previous handlers after a graceful shutdown and then
        # re-raises SIGTERM. With the default handler that kills the process before
        # in-flight notifications are flushed, so install one that does nothing:
        # uvicorn has already done the work of stopping by then.
        signal.signal(signal.SIGTERM, lambda *_: None)

        watchdog = asyncio.create_task(_startup_watchdog(notifier, cfg))
        test_task = (asyncio.create_task(_startup_test(cfg, client))
                     if cfg.server.test_on_start else None)
        log.info("starting v%s: %d channel(s), callback %s", __version__, subscribed,
                 cfg.server.callback_url)
        try:
            await notifier.run(
                host=cfg.server.host, port=cfg.server.port,
                log_level=logging.DEBUG if cfg.server.log_level == "DEBUG" else logging.WARNING,
                # Let uvicorn's logs flow through our handlers (so they reach the
                # log file too) and don't advertise the server software.
                log_config=None, server_header=False,
            )
        finally:
            watchdog.cancel()
            if engine.pending:
                log.info("shutting down; finishing %d in-flight notification(s)",
                         engine.pending)
            try:
                # Docker's default stop grace period is 10s.
                await asyncio.wait_for(engine.drain(), timeout=7)
            except asyncio.TimeoutError:
                log.warning("gave up waiting for in-flight notifications")
            state.save()
            log.info("stopped")


async def _startup_watchdog(notifier: AsyncYouTubeNotifier, cfg: Config) -> None:
    await asyncio.sleep(60)
    if notifier.is_ready:
        return
    log.error(
        "Still not ready after 60s. At startup the notifier requests its OWN public "
        "callback URL (%s) from inside this container and needs an HTTP 200 (no "
        "redirects). Check: DNS, reverse proxy/tunnel forwarding to port %d, https "
        "certificate, and that this container can reach its own public address "
        "(see 'Troubleshooting' in the README).",
        cfg.server.callback_url, cfg.server.port,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ytnoti_discord",
        description="YouTube upload/edit/delete/live notifications -> Discord webhooks.")
    parser.add_argument("-c", "--config", default=os.environ.get("CONFIG_PATH", "/config/config.yaml"))
    parser.add_argument("--check", action="store_true", help="validate the config and exit")
    parser.add_argument("--test", nargs="*", metavar="EVENT", default=None,
                        help=f"send sample messages ({', '.join(EVENT_NAMES)}); "
                             "no event names = all enabled events")
    parser.add_argument("--channel", help="channel ID to use for --test (default: first)")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    # An explicit --test flag still works even if the config says check_only.
    check = args.check or (cfg.server.check_only and args.test is None)
    serving = not check and args.test is None
    # Only the long-running service writes the log file; --check/--test (often run
    # via `docker compose run`) stay on the console and don't fight over rotation.
    setup_logging(
        cfg.server.log_level,
        resolve_log_file(cfg) if serving else None,
        max_bytes=cfg.server.log_max_bytes,
        backup_count=cfg.server.log_backup_count,
    )

    if check:
        print_summary(cfg, config_path)
        if not args.check:
            print("\n(server.check_only is true in the config: not starting the service)")
        print("\nConfig OK")
        return 0
    if args.test is not None:
        bad = [e for e in args.test if e not in EVENT_NAMES]
        if bad:
            print(f"unknown event(s): {', '.join(bad)}", file=sys.stderr)
            return 2
        return asyncio.run(run_test(cfg, args.test, args.channel))

    try:
        asyncio.run(serve(cfg))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
