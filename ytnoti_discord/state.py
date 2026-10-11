"""Small JSON state file: remembers titles and what was already announced."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("ytnoti_discord.state")

MAX_RECORDS = 2000


@dataclass
class VideoRecord:
    title: str = ""
    # Snapshot used as the "before" of the next edit. None = never captured.
    description: str | None = None
    tags: list[str] | None = None
    channel_id: str = ""
    last_updated: str = ""        # ISO timestamp of the newest feed entry handled
    upload_notified: bool = False
    live_notified: bool = False
    upcoming_notified: bool = False
    stream_ended: bool = False
    deleted: bool = False
    last_notified_ts: float = 0.0
    seen: float = field(default_factory=time.time)


class StateStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._records: dict[str, VideoRecord] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.error("state file %s unreadable (%s); starting empty", self._path, exc)
            return
        known = VideoRecord.__dataclass_fields__
        for vid, rec in (raw.get("videos") or {}).items():
            self._records[vid] = VideoRecord(**{k: v for k, v in rec.items() if k in known})
        log.info("loaded state for %d videos", len(self._records))

    def get(self, video_id: str) -> VideoRecord | None:
        return self._records.get(video_id)

    def put(self, video_id: str, record: VideoRecord) -> None:
        record.seen = time.time()
        self._records[video_id] = record

    def save(self) -> None:
        if len(self._records) > MAX_RECORDS:
            keep = sorted(self._records.items(), key=lambda kv: kv[1].seen, reverse=True)
            self._records = dict(keep[:MAX_RECORDS])
        payload = {"videos": {k: asdict(v) for k, v in self._records.items()}}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".state-")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, self._path)
        except OSError as exc:
            log.error("could not write state file %s: %s", self._path, exc)
