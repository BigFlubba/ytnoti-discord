import re
from pathlib import Path

import pytest

from ytnoti_discord.config import EVENT_NAMES, load_config
from ytnoti_discord.render import build_context, render_payload
from conftest import BASE, CHANNEL, HOOK

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.yaml"


def ctx(event, **kw):
    return build_context(
        event=event, video_id="abc", title=kw.pop("title", "A title"),
        url="https://www.youtube.com/watch?v=abc", channel_id=CHANNEL,
        channel_name="Chan", channel_url="https://www.youtube.com/channel/x",
        thumbnail="https://i.ytimg.com/vi/abc/hqdefault.jpg", **kw)


@pytest.mark.parametrize("event", EVENT_NAMES)
def test_every_builtin_event_renders_a_valid_payload(write_cfg, event):
    cfg = load_config(write_cfg())
    p = render_payload(cfg.channels[CHANNEL].events[event], ctx(event, title="T" * 500), cfg.discord)
    assert len(p.get("content", "")) <= 2000
    e = p["embeds"][0]
    assert len(e["title"]) <= 256 and e["color"] > 0
    assert e["author"]["name"] == "Chan"


def test_embed_false_gives_plain_content(write_cfg):
    cfg = load_config(write_cfg(BASE + '\ndefaults:\n  events:\n    upload: {embed: false, content: "{url}"}\n'))
    p = render_payload(cfg.channels[CHANNEL].events["upload"], ctx("upload"), cfg.discord)
    assert "embeds" not in p and p["content"] == "https://www.youtube.com/watch?v=abc"


def test_null_sections_mean_defaults(write_cfg):
    cfg = load_config(write_cfg(BASE + "\ndiscord:\ndefaults:\n  events:\n    edit:\n"))
    assert cfg.channels[CHANNEL].events["edit"].enabled


def test_example_config_is_valid(monkeypatch):
    # The example config references CALLBACK_URL; set a harmless test value so
    # loading the example succeeds. The docs allow an empty/placeholder value:
    # set the env var to an empty string to mirror that behavior.
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", HOOK)
    monkeypatch.setenv("CALLBACK_URL", "")
    load_config(EXAMPLE)

def test_commented_second_channel_example_is_valid_when_uncommented(tmp_path, monkeypatch):
    # As above, ensure CALLBACK_URL is present so the example config can be
    # parsed and validated in tests.
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", HOOK)
    monkeypatch.setenv("DISCORD_ALERTS_WEBHOOK_URL", HOOK)
    monkeypatch.setenv("CALLBACK_URL", "")

    text = EXAMPLE.read_text()

    # Try to locate the commented "second channel" block robustly.
    # We look for the header line that mentions "A second channel" (commented or not),
    # and an ending marker "# ----" if present; otherwise, stop at the next top-level separator.
    start_idx = None
    end_idx = None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "A second channel" in line:
            start_idx = i
            break

    if start_idx is not None:
        # find end marker "# ----" after start, else stop at next line that begins with "# ----" or a top-level separator
        for j in range(start_idx + 1, len(lines)):
            if lines[j].lstrip().startswith("# ----") or lines[j].strip() == "":
                end_idx = j
                break
        if end_idx is None:
            end_idx = len(lines)

        # Uncomment lines in the block (remove a single leading "# " if present)
        for k in range(start_idx, end_idx):
            if lines[k].lstrip().startswith("#"):
                # remove the first occurrence of "# " or "#" while preserving indentation
                lines[k] = re.sub(r"^(\s*)#\s?", r"\1", lines[k])

    # also ensure some commonly-commented keys are uncommented
    text_out = "\n".join(lines)
    text_out = text_out.replace("  # webhooks:", "  webhooks:")
    text_out = text_out.replace("  # alerts:", "  alerts:")

    p = tmp_path / "c.yaml"
    p.write_text(text_out)
    cfg = load_config(p)
    assert len(cfg.channels) == 2