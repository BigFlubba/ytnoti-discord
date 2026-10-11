"""Engine logic with a fake YouTube and a capturing Discord sender."""
import asyncio
import time
from datetime import datetime, timedelta, timezone

from ytnoti import DeletedVideo, Video
from ytnoti.models.video import Channel, Timestamp

from ytnoti_discord.config import load_config
from ytnoti_discord.engine import Engine
from ytnoti_discord.state import StateStore
from ytnoti_discord.youtube import LiveInfo
from conftest import BASE, CHANNEL, HOOK

NOW = datetime.now(timezone.utc)
CH = Channel(id=CHANNEL, name="CBC Feed Name", url=f"https://www.youtube.com/channel/{CHANNEL}")


class FakeSender:
    def __init__(self):
        self.sent = []

    async def send(self, label, url, payload):
        self.sent.append((label, payload))
        return True


class FakeYT:
    def __init__(self):
        self.info = LiveInfo("none")

    async def live_info(self, vid):
        return self.info

    async def thumbnail_url(self, vid):
        return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"


def vid(title="Hello", updated_offset=0, vid_id="VID00000001"):
    return Video(id=vid_id, title=title, url=f"https://www.youtube.com/watch?v={vid_id}",
                 timestamp=Timestamp(published=NOW, updated=NOW + timedelta(seconds=updated_offset)),
                 channel=CH)


def make(tmp_path, write_cfg, extra=""):
    cfg = load_config(write_cfg(BASE + extra))
    sender, yt = FakeSender(), FakeYT()
    return Engine(cfg, sender, yt, StateStore(tmp_path / "state.json")), sender, yt


def events(sender):
    return [p["embeds"][0]["title"] if "embeds" in p else p.get("content") for _, p in sender.sent]


def run(engine, *calls):
    async def go():
        for fn, arg in calls:
            await fn(arg)
            await engine.drain()
    asyncio.run(go())


def test_new_upload(tmp_path, write_cfg):
    e, s, _ = make(tmp_path, write_cfg)
    run(e, (e.on_upload, vid("Brand new")))
    assert len(s.sent) == 1
    p = s.sent[0][1]
    assert "CBC News" in p["content"]                       # configured name wins over feed name
    assert p["embeds"][0]["title"] == "Brand new"
    assert p["embeds"][0]["image"]["url"].endswith("hqdefault.jpg")
    assert p["allowed_mentions"] == {"parse": ["roles", "users"]}


def test_redelivered_upload_push_is_not_announced_twice_or_as_an_edit(tmp_path, write_cfg):
    e, s, _ = make(tmp_path, write_cfg)
    run(e, (e.on_upload, vid()), (e.on_edit, vid()))        # hub redelivers same entry (lib says "edit")
    assert len(s.sent) == 1


def test_edit_shows_title_change_and_respects_cooldown(tmp_path, write_cfg):
    extra = "\ndefaults:\n  events:\n    edit: {cooldown_seconds: 0}\n"
    e, s, _ = make(tmp_path, write_cfg, extra)
    run(e, (e.on_upload, vid("Old title")), (e.on_edit, vid("New title", 60)))
    assert len(s.sent) == 2
    desc = s.sent[1][1]["embeds"][0]["description"]
    assert "Old title" in desc and "New title" in desc

    sub = tmp_path / "default-cooldown"
    sub.mkdir()
    e2, s2, _ = make(sub, write_cfg)
    run(e2, (e2.on_upload, vid("A")), (e2.on_edit, vid("B", 60)))   # default 300s cooldown
    assert len(s2.sent) == 1


def test_only_if_title_changed(tmp_path, write_cfg):
    extra = "\ndefaults:\n  events:\n    edit: {cooldown_seconds: 0, only_if_title_changed: true}\n"
    e, s, _ = make(tmp_path, write_cfg, extra)
    run(e, (e.on_upload, vid("Same")), (e.on_edit, vid("Same", 60)), (e.on_edit, vid("Different", 120)))
    assert len(s.sent) == 2
    assert "Different" in s.sent[1][1]["embeds"][0]["description"]


def test_scheduled_stream_is_not_announced_as_upload_then_live_is_announced_once(tmp_path, write_cfg):
    e, s, yt = make(tmp_path, write_cfg)
    yt.info = LiveInfo("upcoming", 1791828000)
    run(e, (e.on_upload, vid("Big stream")))
    assert s.sent == []                                     # silent while scheduled

    yt.info = LiveInfo("live")
    run(e, (e.on_edit, vid("Big stream", 30)))              # goes live -> "edit" push
    assert len(s.sent) == 1 and "live now" in s.sent[0][1]["content"]

    run(e, (e.on_edit, vid("Big stream", 30)), (e.on_edit, vid("Big stream", 90)))
    assert len(s.sent) == 1                                 # no repeat live pings


def test_stream_that_is_live_on_first_push(tmp_path, write_cfg):
    e, s, yt = make(tmp_path, write_cfg)
    yt.info = LiveInfo("live")
    run(e, (e.on_upload, vid("Instant live")))
    assert len(s.sent) == 1 and "live now" in s.sent[0][1]["content"]   # live, not "uploaded"


def test_stream_end_is_not_reported_as_an_edit(tmp_path, write_cfg):
    extra = "\ndefaults:\n  events:\n    edit: {cooldown_seconds: 0}\n"
    e, s, yt = make(tmp_path, write_cfg, extra)
    yt.info = LiveInfo("live")
    run(e, (e.on_upload, vid("Stream")))
    yt.info = LiveInfo("none")
    run(e, (e.on_edit, vid("Stream", 7200)))                # stream ended
    assert len(s.sent) == 1
    run(e, (e.on_edit, vid("Stream (VOD)", 7300)))          # a real later edit still gets through
    assert len(s.sent) == 2


def test_upcoming_event_when_enabled(tmp_path, write_cfg):
    e, s, yt = make(tmp_path, write_cfg, "\ndefaults:\n  events:\n    upcoming: {enabled: true}\n")
    yt.info = LiveInfo("upcoming", 1791828000)
    run(e, (e.on_upload, vid("Soon")), (e.on_edit, vid("Soon", 5)))
    assert len(s.sent) == 1
    assert "<t:1791828000:R>" in s.sent[0][1]["embeds"][0]["description"]


def test_unknown_live_status_degrades_to_library_classification(tmp_path, write_cfg):
    e, s, yt = make(tmp_path, write_cfg)
    yt.info = None
    run(e, (e.on_upload, vid("X")))
    assert len(s.sent) == 1 and "uploaded" in s.sent[0][1]["content"]


def test_delete_uses_remembered_title_and_is_deduplicated(tmp_path, write_cfg):
    e, s, _ = make(tmp_path, write_cfg)
    run(e, (e.on_upload, vid("Doomed video")))
    gone = DeletedVideo(id="VID00000001", url="https://www.youtube.com/watch?v=VID00000001",
                        timestamp=NOW, channel=CH)
    run(e, (e.on_delete, gone), (e.on_delete, gone))
    assert len(s.sent) == 2
    assert "Doomed video" in s.sent[1][1]["embeds"][0]["description"]


def test_state_survives_restart(tmp_path, write_cfg):
    e, s, _ = make(tmp_path, write_cfg)
    run(e, (e.on_upload, vid("Persisted")))
    e2, s2, _ = make(tmp_path, write_cfg)                   # same state.json
    run(e2, (e2.on_upload, vid("Persisted")))               # same push after restart
    assert s2.sent == []


def test_title_cannot_ping_everyone(tmp_path, write_cfg):
    e, s, _ = make(tmp_path, write_cfg,
                   '\ndefaults:\n  events:\n    upload: {content: "new: {title}"}\n')
    run(e, (e.on_upload, vid("@everyone free stuff <@&123>")))
    content = s.sent[0][1]["content"]
    assert "@everyone" not in content and "<@&123>" not in content
    assert s.sent[0][1]["embeds"][0]["title"].startswith("@everyone")   # embed shows it verbatim


def test_unconfigured_channel_ignored(tmp_path, write_cfg):
    e, s, _ = make(tmp_path, write_cfg)
    other = Video(id="V2", title="t", url="u", timestamp=Timestamp(NOW, NOW),
                  channel=Channel(id="UCother", name="o", url="u"))
    run(e, (e.on_upload, other))
    assert s.sent == []


def test_hub_redelivery_is_deduplicated_even_with_no_cooldown(tmp_path, write_cfg):
    """Only the duplicate-by-`updated` check can stop these (cooldown is 0)."""
    extra = "\ndefaults:\n  events:\n    edit: {cooldown_seconds: 0}\n"
    e, s, _ = make(tmp_path, write_cfg, extra)
    run(e, (e.on_upload, vid("Title")),
        (e.on_edit, vid("Title")),                 # same entry delivered again
        (e.on_edit, vid("Renamed", 60)),           # genuine edit
        (e.on_edit, vid("Renamed", 60)))           # ...delivered twice
    assert len(s.sent) == 2
    assert "uploaded" in s.sent[0][1]["content"]
    assert "Renamed" in s.sent[1][1]["embeds"][0]["description"]
