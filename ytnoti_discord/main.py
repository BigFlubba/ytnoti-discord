"""Entrypoint: run the notifier, validate the config, or send test messages."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time

import httpx
from fastapi import FastAPI, Response
from ytnoti import AsyncYouTubeNotifier
from ytnoti.models.history import FileVideoHistory

from . import __version__
from .config import EVENT_NAMES, Config, ConfigError, load_config
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


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # httpx logs every request URL at INFO -- that would print Discord webhook
    # tokens and the YouTube API key. Keep these quiet.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("AsyncYouTubeNotifier").addFilter(_Throttle())


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
    print(f"Live detect  : {'YouTube Data API' if cfg.youtube_api_key else 'watch-page scrape (no API key)'}")
    print(f"Channels     : {len(cfg.channels)}")
    for ch in cfg.channels.values():
        enabled = [e for e in EVENT_NAMES if ch.events[e].enabled]
        hooks = sorted({label for e in enabled for label, _ in ch.targets[e]})
        print(f"  - {ch.id} {ch.name or ''}".rstrip())
        print(f"      events : {', '.join(enabled) or 'none'}")
        print(f"      sends to: {', '.join(hooks) or 'none'}")


async def run_test(cfg: Config, events: list[str], channel_id: str | None) -> int:
    if channel_id:
        if channel_id not in cfg.channels:
            print(f"error: channel {channel_id} is not in the config", file=sys.stderr)
            return 2
        channel = cfg.channels[channel_id]
    else:
        channel = next(iter(cfg.channels.values()))
    events = events or [e for e in EVENT_NAMES if channel.events[e].enabled]

    sample = "jNQXAC9IVRw"  # the first video ever uploaded to YouTube
    failures = 0
    async with _http_client() as client:
        sender = DiscordSender(client, cfg.discord.max_retries)
        for event in events:
            ev = channel.events[event]
            if not channel.targets[event]:
                print(f"- {event}: skipped (disabled)")
                continue
            ctx = build_context(
                event=event, video_id=sample, title=f"[TEST] Sample {event} notification",
                url=f"https://www.youtube.com/watch?v={sample}",
                channel_id=channel.id, channel_name=channel.name or "Test Channel",
                channel_url=f"https://www.youtube.com/channel/{channel.id}",
                old_title="[TEST] Old title",
                change="Title changed:\n~~[TEST] Old title~~\n\u2192 **[TEST] New title**",
                thumbnail=f"https://i.ytimg.com/vi/{sample}/hqdefault.jpg",
            )
            payload = render_payload(ev, ctx, cfg.discord)
            ok = all([await sender.send(label, url, payload)
                      for label, url in channel.targets[event]])
            print(f"- {event}: {'sent' if ok else 'FAILED'}")
            failures += 0 if ok else 1
    return 1 if failures else 0


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
        log.info("starting v%s: %d channel(s), callback %s", __version__, subscribed,
                 cfg.server.callback_url)
        try:
            await notifier.run(
                host=cfg.server.host, port=cfg.server.port,
                log_level=logging.DEBUG if cfg.server.log_level == "DEBUG" else logging.WARNING,
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

    setup_logging(cfg.server.log_level)

    if args.check:
        print_summary(cfg)
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
