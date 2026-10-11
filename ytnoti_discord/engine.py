"""Decides which notification (if any) a YouTube push should produce."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Awaitable, Coroutine, Protocol

from ytnoti import DeletedVideo, Video

from .config import Channel, Config
from .discord_client import DiscordSender
from .render import build_context, render_payload
from .state import StateStore, VideoRecord
from .youtube import LiveInfo

log = logging.getLogger("ytnoti_discord.engine")


class _YouTube(Protocol):
    async def live_info(self, video_id: str) -> LiveInfo | None: ...
    async def thumbnail_url(self, video_id: str) -> str: ...


class Engine:
    def __init__(self, cfg: Config, sender: DiscordSender, yt: _YouTube,
                 state: StateStore) -> None:
        self.cfg = cfg
        self.sender = sender
        self.yt = yt
        self.state = state
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tasks: set[asyncio.Task[Any]] = set()

    # -- listeners registered on the notifier --------------------------------
    # YouTube's hub expects a quick HTTP response, so the real work (live
    # check, thumbnail lookup, Discord calls) runs in a background task.

    async def on_upload(self, video: Video) -> None:
        self._spawn(self._handle_video(video, "upload"))

    async def on_edit(self, video: Video) -> None:
        self._spawn(self._handle_video(video, "edit"))

    async def on_delete(self, video: DeletedVideo) -> None:
        self._spawn(self._handle_delete(video))

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(self._guard(coro))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @property
    def pending(self) -> int:
        return len(self._tasks)

    async def drain(self) -> None:
        """Wait for in-flight notifications (used on shutdown and in tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    @staticmethod
    async def _guard(coro: Awaitable[None]) -> None:
        try:
            await coro
        except Exception:  # never let one bad event kill the listener
            log.exception("unhandled error while processing notification")

    # -- upload / edit / live / upcoming -------------------------------------

    async def _handle_video(self, video: Video, lib_kind: str) -> None:
        channel = self.cfg.channels.get(video.channel.id)
        if channel is None:
            log.warning("push for unconfigured channel %s ignored", video.channel.id)
            return

        async with self._locks[video.id]:
            rec = self.state.get(video.id) or VideoRecord(channel_id=video.channel.id)
            old_title = rec.title
            title_changed = bool(old_title) and old_title != video.title
            duplicate = bool(rec.last_updated) and video.timestamp.updated <= (
                datetime.fromisoformat(rec.last_updated))

            info = await self.yt.live_info(video.id)
            status = info.status if info else None
            log.info("video %s (%s): lib=%s live_status=%s duplicate=%s",
                     video.id, video.channel.name, lib_kind, status, duplicate)

            event = self._choose_event(channel, rec, lib_kind, status, duplicate,
                                       title_changed)

            if event:
                change = (f"Title changed:\n~~{old_title}~~\n\u2192 **{video.title}**"
                          if title_changed else "The video details were updated.")
                ctx = build_context(
                    event=event, video_id=video.id, title=video.title, url=video.url,
                    channel_id=video.channel.id,
                    channel_name=channel.name or video.channel.name,
                    channel_url=video.channel.url, old_title=old_title, change=change,
                    published=video.timestamp.published, updated=video.timestamp.updated,
                    scheduled_ts=info.scheduled_ts if info else None,
                    thumbnail=await self._thumbnail(channel, event, video.id),
                )
                if await self._dispatch(channel, event, ctx):
                    self._mark(rec, event)

            rec.title = video.title
            rec.channel_id = video.channel.id
            if not duplicate:
                rec.last_updated = video.timestamp.updated.isoformat()
            self.state.put(video.id, rec)
            self.state.save()

    def _choose_event(self, channel: Channel, rec: VideoRecord, lib_kind: str,
                      status: str | None, duplicate: bool,
                      title_changed: bool) -> str | None:
        """Pure decision logic; returns an event name or None to stay quiet."""
        ev = channel.events

        # A stream going live is announced once, even if YouTube re-sends the
        # same feed entry (so `duplicate` must not suppress it).
        if status == "live" and not rec.live_notified and ev["live"].enabled:
            return "live"

        if duplicate:
            return None

        if status == "upcoming":
            # Never announce a not-yet-started stream as a normal upload.
            if ev["upcoming"].enabled and not rec.upcoming_notified:
                return "upcoming"
            return None

        if lib_kind == "upload" and not rec.upload_notified and not rec.live_notified:
            return "upload" if ev["upload"].enabled else None

        # Everything else is an edit.
        edit = ev["edit"]
        if not edit.enabled:
            return None
        # YouTube touches a stream's feed entry when it ends; that is not a
        # user edit, so swallow it unless the title also changed.
        if rec.live_notified and status == "none" and not rec.stream_ended:
            rec.stream_ended = True
            if not title_changed:
                return None
        if edit.only_if_title_changed and not title_changed:
            return None
        if time.time() - rec.last_notified_ts < edit.cooldown_seconds:
            return None
        return "edit"

    @staticmethod
    def _mark(rec: VideoRecord, event: str) -> None:
        rec.last_notified_ts = time.time()
        if event == "upload":
            rec.upload_notified = True
        elif event == "live":
            rec.live_notified = True
        elif event == "upcoming":
            rec.upcoming_notified = True

    # -- delete ----------------------------------------------------------------

    async def _handle_delete(self, video: DeletedVideo) -> None:
        channel = self.cfg.channels.get(video.channel.id)
        if channel is None:
            log.warning("delete for unconfigured channel %s ignored", video.channel.id)
            return

        async with self._locks[video.id]:
            rec = self.state.get(video.id) or VideoRecord(channel_id=video.channel.id)
            if rec.deleted:
                log.info("duplicate delete push for %s ignored", video.id)
                return

            if channel.events["delete"].enabled:
                ctx = build_context(
                    event="delete", video_id=video.id,
                    title=rec.title or "(title unknown)", url=video.url,
                    channel_id=video.channel.id,
                    channel_name=channel.name or video.channel.name,
                    channel_url=video.channel.url, deleted_at=video.timestamp,
                    thumbnail=await self._thumbnail(channel, "delete", video.id),
                )
                await self._dispatch(channel, "delete", ctx)

            rec.deleted = True
            self.state.put(video.id, rec)
            self.state.save()

    # -- shared ------------------------------------------------------------------

    async def _thumbnail(self, channel: Channel, event: str, video_id: str) -> str:
        embed = channel.events[event].embed
        if embed is None or not (embed.image or embed.thumbnail):
            return ""
        return await self.yt.thumbnail_url(video_id)

    async def _dispatch(self, channel: Channel, event: str, ctx: dict[str, str]) -> bool:
        payload = render_payload(channel.events[event], ctx, self.cfg.discord)
        delivered = False
        for label, url in channel.targets[event]:
            if await self.sender.send(label, url, payload):
                delivered = True
        log.info("%s notification for %s -> %s", event, ctx["video_id"],
                 "sent" if delivered else "FAILED")
        return delivered
