import pytest

from ytnoti_discord.config import ConfigError, load_config
from conftest import BASE, CHANNEL, HOOK, HOOK2


def test_minimal_config_gets_sensible_defaults(write_cfg):
    cfg = load_config(write_cfg())
    ch = cfg.channels[CHANNEL]
    assert [e for e, v in ch.events.items() if v.enabled] == ["upload", "live", "edit", "delete"]
    assert not ch.events["upcoming"].enabled          # off unless asked for
    assert ch.targets["upload"] == [("main", HOOK)]    # single webhook auto-selected
    assert cfg.discord.allowed_mentions == {"parse": []}


def test_env_interpolation_and_default(write_cfg, monkeypatch):
    monkeypatch.setenv("MY_HOOK", HOOK2)
    text = BASE.replace(HOOK, "${MY_HOOK}") + "\nyoutube_api_key: ${NOPE:-fallback}\n"
    cfg = load_config(write_cfg(text))
    assert cfg.channels[CHANNEL].targets["upload"][0][1] == HOOK2
    assert cfg.youtube_api_key == "fallback"


def test_missing_env_var_is_a_clear_error(write_cfg, monkeypatch):
    monkeypatch.delenv("MY_HOOK", raising=False)
    with pytest.raises(ConfigError, match="MY_HOOK"):
        load_config(write_cfg(BASE.replace(HOOK, "${MY_HOOK}")))


def test_rejects_handles_and_bad_ids(write_cfg):
    with pytest.raises(ConfigError, match="not a channel ID"):
        load_config(write_cfg(BASE.replace(CHANNEL, "\"@cbcnews\"")))


def test_rejects_non_discord_webhook(write_cfg):
    with pytest.raises(ConfigError, match="not a Discord webhook"):
        load_config(write_cfg(BASE.replace(HOOK, "https://example.com/hook")))


def test_unknown_keys_are_typos_not_silently_ignored(write_cfg):
    with pytest.raises(ConfigError, match="webhookz|extra"):
        load_config(write_cfg(BASE + "\ndefaults:\n  webhookz: [main]\n"))


def test_bad_template_variable_fails_at_startup(write_cfg):
    text = BASE + """
defaults:
  events:
    upload:
      content: "hi {nonexistent}"
"""
    with pytest.raises(ConfigError, match="bad template"):
        load_config(write_cfg(text))


def test_per_channel_override_and_routing(write_cfg):
    text = f"""
server: {{callback_url: "https://yt.example.com"}}
webhooks: {{main: {HOOK}, alerts: {HOOK2}}}
defaults:
  webhooks: [main]
  events:
    edit: {{enabled: false}}
channels:
  - id: {CHANNEL}
    events:
      live: {{webhooks: [alerts, main], content: "LIVE {{title}}"}}
      edit: {{enabled: true, only_if_title_changed: true}}
      delete: {{enabled: false}}
      upload: {{embed: false}}
"""
    ch = load_config(write_cfg(text)).channels[CHANNEL]
    assert [l for l, _ in ch.targets["live"]] == ["alerts", "main"]
    assert ch.targets["upload"][0][0] == "main"
    assert ch.events["edit"].enabled and ch.events["edit"].only_if_title_changed
    assert not ch.events["delete"].enabled
    assert ch.events["upload"].embed is None
    assert ch.events["live"].content == "LIVE {title}"


def test_multiple_webhooks_without_routing_is_an_error(write_cfg):
    text = f"""
server: {{callback_url: "https://yt.example.com"}}
webhooks: {{a: {HOOK}, b: {HOOK2}}}
channels: [{{id: {CHANNEL}}}]
"""
    with pytest.raises(ConfigError, match="no webhook configured"):
        load_config(write_cfg(text))


def test_hex_color_string(write_cfg):
    text = BASE + """
defaults:
  events:
    upload:
      embed: {color: "#00FF88"}
"""
    assert load_config(write_cfg(text)).channels[CHANNEL].events["upload"].embed.color == 0x00FF88
