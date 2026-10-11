"""CLI helpers: test notifications for every event, and file logging."""
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from ytnoti_discord import main as app_main
from ytnoti_discord.config import EVENT_NAMES, load_config
from ytnoti_discord.render import render_payload
from conftest import BASE, CHANNEL, HOOK, HOOK2


# -- test notifications ------------------------------------------------------------

def run_tests_capturing(cfg, events=(), channel=None, fail_for=()):
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append((str(req.url), req.read()))
        return httpx.Response(500 if str(req.url) in fail_for else 204)

    def client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    orig = app_main._http_client
    app_main._http_client = client
    try:
        code = asyncio.run(app_main.run_test(cfg, list(events), channel))
    finally:
        app_main._http_client = orig
    return code, sent


def test_default_test_run_covers_all_events_including_disabled(write_cfg, capsys):
    cfg = load_config(write_cfg())
    assert not cfg.channels[CHANNEL].events["upcoming"].enabled
    code, sent = run_tests_capturing(cfg)
    out = capsys.readouterr().out
    assert code == 0 and len(sent) == len(EVENT_NAMES)
    for event in EVENT_NAMES:
        assert f"- {event}: sent" in out
    assert "upcoming: sent (event is disabled in config" in out


def test_single_disabled_event_can_be_tested(write_cfg):
    code, sent = run_tests_capturing(load_config(write_cfg()), ["upcoming"])
    assert code == 0 and len(sent) == 1


def test_messages_are_labelled_as_test_and_never_ping(write_cfg):
    cfg = load_config(write_cfg(BASE + """
defaults:
  events:
    live: {content: "<@&123456789012345678> {channel_name} is LIVE", embed: false}
    upload: {content: "{url}", embed: false}
"""))
    ch = cfg.channels[CHANNEL]
    for event in ("live", "upload"):
        ctx = app_main.build_test_context(ch, event)
        payload = app_main.mark_as_test(render_payload(ch.events[event], ctx, cfg.discord),
                                        event)
        assert payload["content"].startswith(f"\U0001F9EA **[TEST: {event}]**")
        assert payload["allowed_mentions"] == {"parse": []}


def test_sample_contexts_fit_each_event():
    from ytnoti_discord.config import Channel
    ch = Channel(id=CHANNEL, name=None, events={})
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ts = int(now.timestamp())
    assert int(app_main.build_test_context(ch, "upcoming", now)["scheduled_ts"]) == ts + 3600
    assert app_main.build_test_context(ch, "delete", now)["deleted_ts"] == str(ts)
    edit = app_main.build_test_context(ch, "edit", now)
    assert edit["old_title"] and "Title changed" in edit["change"]
    assert all(app_main.build_test_context(ch, e)["channel_name"] == "Test Channel"
               for e in EVENT_NAMES)


def test_failures_are_reported_and_set_exit_code(write_cfg, capsys, monkeypatch):
    async def no_sleep(_seconds):
        return None
    monkeypatch.setattr(asyncio, "sleep", no_sleep)  # skip Discord retry back-off
    cfg = load_config(write_cfg())
    code, _ = run_tests_capturing(cfg, ["upload"], fail_for=(HOOK,))
    assert code == 1 and "upload: FAILED" in capsys.readouterr().out


def test_routing_is_respected_for_disabled_events(write_cfg):
    text = f"""
server: {{callback_url: "https://yt.example.com"}}
webhooks: {{main: {HOOK}, alerts: {HOOK2}}}
defaults: {{webhooks: [main]}}
channels:
  - id: {CHANNEL}
    events:
      upcoming: {{webhooks: [alerts]}}
"""
    _, sent = run_tests_capturing(load_config(write_cfg(text)), ["upcoming"])
    assert [u for u, _ in sent] == [HOOK2]


def test_disabled_event_with_bad_webhook_still_loads(write_cfg):
    text = BASE + """
defaults:
  events:
    delete: {enabled: false, webhooks: [typo]}
"""
    cfg = load_config(write_cfg(text))
    assert cfg.channels[CHANNEL].test_targets["delete"] == []


def test_unknown_channel_is_an_error(write_cfg, capsys):
    code, sent = run_tests_capturing(load_config(write_cfg()), channel="UC" + "z" * 22)
    assert code == 2 and sent == []


# -- file logging ------------------------------------------------------------------

@pytest.fixture
def clean_logging():
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    yield
    for h in root.handlers[:]:
        root.removeHandler(h)
        h.close()
    for h in saved[0]:
        root.addHandler(h)
    root.setLevel(saved[1])


def test_logs_are_written_to_the_file(tmp_path, clean_logging):
    log_file = tmp_path / "logs" / "app.log"
    app_main.setup_logging("INFO", log_file)
    logging.getLogger("ytnoti_discord.test").info("hello file")
    logging.getLogger("ytnoti_discord.test").debug("hidden at INFO")
    for h in logging.getLogger().handlers:
        h.flush()
    text = log_file.read_text(encoding="utf-8")
    assert "hello file" in text and "hidden at INFO" not in text


def test_secrets_never_reach_the_log_file(tmp_path, clean_logging):
    log_file = tmp_path / "app.log"
    app_main.setup_logging("INFO", log_file)
    log = logging.getLogger("x")
    log.error("failed POST %s", HOOK)
    log.error("GET https://www.googleapis.com/youtube/v3/videos?id=1&key=AIzaSECRET123&part=x")
    for h in logging.getLogger().handlers:
        h.flush()
    text = log_file.read_text(encoding="utf-8")
    assert "abcDEF_ghi-123" not in text and "AIzaSECRET123" not in text
    assert "<redacted>" in text


def test_file_is_rotated(tmp_path, clean_logging):
    log_file = tmp_path / "app.log"
    app_main.setup_logging("INFO", log_file, max_bytes=10_000, backup_count=2)
    for i in range(400):
        logging.getLogger("x").info("line %d %s", i, "y" * 60)
    assert (tmp_path / "app.log.1").exists()
    assert not (tmp_path / "app.log.3").exists()


def test_repeated_setup_does_not_duplicate_output(tmp_path, clean_logging):
    for _ in range(3):
        app_main.setup_logging("INFO", tmp_path / "app.log")
    assert len(logging.getLogger().handlers) == 2  # console + file


def test_unwritable_log_file_falls_back_to_console(tmp_path, clean_logging, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    app_main.setup_logging("INFO", blocker / "sub" / "app.log")  # parent is a file
    assert "logging to the console only" in capsys.readouterr().err
    assert len(logging.getLogger().handlers) == 1


def test_log_file_config_and_relative_path(write_cfg, tmp_path):
    cfg = load_config(write_cfg(BASE.replace(
        "callback_url: https://yt.example.com/ytnoti",
        "callback_url: https://yt.example.com/ytnoti\n  log_file: logs/app.log\n"
        "  log_backup_count: 5") + f"\ndata_dir: {tmp_path}\n"))
    assert app_main.resolve_log_file(cfg) == tmp_path / "logs" / "app.log"
    assert cfg.server.log_backup_count == 5
    abs_cfg = load_config(write_cfg(BASE.replace(
        "callback_url: https://yt.example.com/ytnoti",
        "callback_url: https://yt.example.com/ytnoti\n  log_file: /var/log/x.log")))
    assert app_main.resolve_log_file(abs_cfg) == Path("/var/log/x.log")


def test_empty_log_file_means_disabled(write_cfg):
    cfg = load_config(write_cfg(BASE.replace(
        "callback_url: https://yt.example.com/ytnoti",
        'callback_url: https://yt.example.com/ytnoti\n  log_file: ""')))
    assert app_main.resolve_log_file(cfg) is None


def test_only_the_service_writes_the_log_file(write_cfg, tmp_path, clean_logging, capsys):
    log_file = tmp_path / "svc.log"
    cfg_path = write_cfg(BASE.replace(
        "callback_url: https://yt.example.com/ytnoti",
        f"callback_url: https://yt.example.com/ytnoti\n  log_file: {log_file}"))
    assert app_main.main(["-c", str(cfg_path), "--check"]) == 0
    assert not log_file.exists()
    assert f"Log file     : {log_file}" in capsys.readouterr().out


# -- server.test_on_start ----------------------------------------------------------

def cfg_with(write_cfg, value: str):
    return load_config(write_cfg(BASE.replace(
        "callback_url: https://yt.example.com/ytnoti",
        f"callback_url: https://yt.example.com/ytnoti\n  test_on_start: {value}")))


@pytest.mark.parametrize("value, expected", [
    ("true", True), ("false", False), ("[]", False), ("[upload, live, upload]", ["upload", "live"]),
    ('"upload, edit"', ["upload", "edit"]), ('"true"', True), ('""', False), ('"off"', False),
])
def test_test_on_start_accepted_forms(write_cfg, value, expected):
    assert cfg_with(write_cfg, value).server.test_on_start == expected


def test_test_on_start_defaults_to_off(write_cfg):
    assert load_config(write_cfg()).server.test_on_start is False


def test_test_on_start_rejects_unknown_events(write_cfg):
    from ytnoti_discord.config import ConfigError
    with pytest.raises(ConfigError, match="unknown event"):
        cfg_with(write_cfg, "[upload, bogus]")


def run_startup_test(cfg, fail=False):
    sent = []

    def handler(req):
        sent.append(str(req.url))
        return httpx.Response(500 if fail else 204)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await app_main._startup_test(cfg, c)
    asyncio.run(go())
    return sent


def test_startup_test_true_sends_every_event(write_cfg, caplog):
    caplog.set_level(logging.INFO)
    sent = run_startup_test(cfg_with(write_cfg, "true"))
    assert len(sent) == len(EVENT_NAMES)
    assert "test_on_start: upcoming sent" in caplog.text
    assert "abcDEF_ghi-123" not in caplog.text


def test_startup_test_list_sends_only_those(write_cfg):
    assert len(run_startup_test(cfg_with(write_cfg, "[live, delete]"))) == 2


def test_startup_test_failure_is_logged_not_raised(write_cfg, monkeypatch, caplog):
    async def no_sleep(_s):
        return None
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    run_startup_test(cfg_with(write_cfg, "[upload]"), fail=True)
    assert "test_on_start: upload failed" in caplog.text


def test_check_mentions_test_on_start(write_cfg, capsys):
    p = write_cfg(BASE.replace("callback_url: https://yt.example.com/ytnoti",
                               "callback_url: https://yt.example.com/ytnoti\n  test_on_start: [live]"))
    assert app_main.main(["-c", str(p), "--check"]) == 0
    assert "Test on start: live" in capsys.readouterr().out


# -- server.check_only and config file lookup --------------------------------------

def cfg_text(extra: str) -> str:
    return BASE.replace("callback_url: https://yt.example.com/ytnoti",
                        f"callback_url: https://yt.example.com/ytnoti\n  {extra}")


def test_check_only_in_config_validates_and_exits(write_cfg, capsys, monkeypatch):
    started = []
    monkeypatch.setattr(app_main, "serve", lambda cfg: started.append(cfg))
    code = app_main.main(["-c", str(write_cfg(cfg_text("check_only: true")))])
    out = capsys.readouterr().out
    assert code == 0 and not started
    assert "Config OK" in out and "check_only is true" in out


def test_check_only_defaults_to_off_and_service_starts(write_cfg, monkeypatch):
    started = []

    async def fake_serve(cfg):
        started.append(cfg)
    monkeypatch.setattr(app_main, "serve", fake_serve)
    assert app_main.main(["-c", str(write_cfg())]) == 0
    assert len(started) == 1


def test_check_flag_still_works_without_config_option(write_cfg, capsys):
    assert app_main.main(["-c", str(write_cfg()), "--check"]) == 0
    out = capsys.readouterr().out
    assert "Config OK" in out and "check_only is true" not in out


def test_explicit_test_flag_wins_over_check_only(write_cfg, monkeypatch):
    ran = []

    async def fake_run_test(cfg, events, channel):
        ran.append(events)
        return 0
    monkeypatch.setattr(app_main, "run_test", fake_run_test)
    assert app_main.main(["-c", str(write_cfg(cfg_text("check_only: true"))),
                          "--test", "upload"]) == 0
    assert ran == [["upload"]]


def test_check_summary_shows_the_config_file_used(write_cfg, capsys):
    p = write_cfg()
    app_main.main(["-c", str(p), "--check"])
    assert f"Config file  : {p}" in capsys.readouterr().out


def test_config_lookup_prefers_project_config_first(tmp_path):
    (tmp_path / "config.yaml").write_text("x")
    assert app_main.resolve_config_path(cwd=tmp_path) == str(tmp_path / "config.yaml")


def test_config_lookup_order_and_overrides(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("x")
    # explicit -c beats everything, then $CONFIG_PATH, then the project config
    assert app_main.resolve_config_path("a.yaml", env="b.yaml", cwd=tmp_path) == "a.yaml"
    assert app_main.resolve_config_path(None, env="b.yaml", cwd=tmp_path) == "b.yaml"
    # nothing found anywhere -> the Docker default (so the error names a real path)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(app_main.Path, "is_file", lambda self: False)
    assert app_main.resolve_config_path(cwd=empty) == "/config/config.yaml"


def test_cli_uses_project_config_without_c_flag(write_cfg, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CONFIG_PATH", raising=False)
    write_cfg()  # writes tmp_path/config.yaml
    monkeypatch.chdir(tmp_path)
    assert app_main.main(["--check"]) == 0
    assert f"Config file  : {tmp_path / 'config.yaml'}" in capsys.readouterr().out


def test_test_notification_for_edit_shows_a_real_before_after():
    from ytnoti_discord.config import Channel
    ch = Channel(id=CHANNEL, name=None, events={})
    change = app_main.build_test_context(ch, "edit")["change"]
    assert change.count("changed:") == 3 and "```diff" in change
