# Configuration

This document covers files and precedence, environment variables, YAML settings, supported event types, message-template variables, environment-variable interpolation, validation behavior, live and upcoming detection, state, logging, & delivery, and command-line options for [ytnoti-discord](https://github.com/BigFlubba/ytnoti-discord).

## Table of Contents

- [Configuration](#configuration)
  - [Table of Contents](#table-of-contents)
  - [Files and precedence](#files-and-precedence)
  - [Environment variables](#environment-variables)
  - [YAML options](#yaml-options)
    - [Required](#required)
    - [Examples](#examples)
      - [Notes about the example](#notes-about-the-example)
    - [Configuration keys](#configuration-keys)
      - [Root-level keys](#root-level-keys)
      - [`server`](#server)
      - [`discord`](#discord)
      - [`webhooks`](#webhooks)
      - [`defaults`](#defaults)
      - [`channels[]`](#channels)
    - [Supported event types](#supported-event-types)
      - [Event settings](#event-settings)
      - [Embed settings](#embed-settings)
    - [Message-template variables](#message-template-variables)
      - [Template formatting rules](#template-formatting-rules)
    - [Environment-variable interpolation](#environment-variable-interpolation)
      - [Required variable](#required-variable)
      - [Optional variable](#optional-variable)
    - [Override and merge behavior](#override-and-merge-behavior)
  - [Validation](#validation)
  - [Live and upcoming detection](#live-and-upcoming-detection)
  - [State, logging, and delivery](#state-logging-and-delivery)
  - [Command-line options](#command-line-options)

## Files and precedence

The configuration file is selected in this order:

1. `-c` / `--config` command-line argument.
2. `CONFIG_PATH` environment variable.
3. `./config.yaml` in the current working directory.
4. `config.yaml` in the project directory.
5. `/config/config.yaml`.

Use `--check` to see which file was loaded and validate it.

---

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DISCORD_WEBHOOK_URL` | Yes for the default example | Discord webhook URL referenced by `webhooks.main`. |
| `YOUTUBE_API_KEY` | No | YouTube Data API v3 key. Improves live/upcoming detection; the API lookup costs one quota unit per push. Without it, the service attempts to read the public watch page. |
| `YTNOTI_SECRET` | No | Fixed HMAC secret. Use it when signatures should remain stable across restarts. If unset, a random secret is generated at startup. |
| `CONFIG_PATH` | No | Path to the YAML config file when no `-c` / `--config` argument is supplied. The Docker image sets its own default. |

Environment-variable interpolation uses `${NAME}` or `${NAME:-default}`. The fallback is used when the variable is unset or empty. Keep real webhook URLs and API keys out of source control and logs.

---

## YAML options

> [!IMPORTANT]
> Save as `config.yaml`. Replace example URLs, webhook destinations, channel IDs, and other placeholders with your own values.
>
> All secrets are to be set in your `.env` file by default. Secret values such as `${RANDOM_VALUE}` can be overidden, but it is not recommended to keep secrets safe.

### Required

> [!WARNING]
> The service will not run without these values set.

```yaml
server:
  # Required: publicly reachable callback URL.
  callback_url: "${CALLBACK_URL}"

# Named Discord webhook(s) and their URL's.
webhooks:
  main: "${DISCORD_WEBHOOK_URL}"

# YouTube channels to monitor.
channels:
  - id: UC7YMZb0X_W06ToOazhFuXIQ
```

### Examples

A collection of examples based on your setup.

<details>
  <summary>Basic (click to open)</summary>

```yaml
server:
  # Required: publicly reachable callback URL.
  callback_url: "${CALLBACK_URL}"

  # Optional shared secret for the notification callback.
  # Empty/null means the application generates a random secret.
  secret: "${YTNOTI_SECRET:-}"

  # Optional rotating log file; relative paths are under data_dir.
  log_file: "${YTNOTI_LOG_FILE:-}"

# Optional YouTube Data API v3 key for live/upcoming detection.
youtube_api_key: "${YOUTUBE_API_KEY:-}"

discord:
  # Optional default webhook display name. Overrides Discord webhook defaults.
  # username: "YouTube Notifications"

  # Optional default webhook avatar URL. Overrides Discord webhook defaults.
  # avatar_url: "https://example.com/avatar.png"

  # Discord allowed-mentions configuration.
  # Supported values are `here`, `everyone`, `<@USER_ID>`, & `<@&ROLE_ID>`
  # Must have Discord developer mode enabled to copy ID's
  allowed_mentions:
    parse: []

# Named Discord webhook(s) and their URL's.
webhooks:
  main: "${DISCORD_WEBHOOK_URL}"

# Shared settings inherited by channels unless overridden.
defaults:
  webhooks:
    - main

# YouTube channels to monitor.
channels:
  - id: "UCuFFtHWoLl5fauMMD5Ww2jA"
    name: "CBC News"
```

</details>

<details>
  <summary>Full (click to open)</summary>

```yaml
server:
  # Required: publicly reachable callback URL.
  callback_url: "${CALLBACK_URL}"

  # HTTP listener.
  host: "0.0.0.0"
  port: 8000

  # Optional shared secret for the notification callback.
  # Empty/null means the application generates a random secret.
  secret: "${YTNOTI_SECRET:-}"

  # Skip YouTube channel ID verification when necessary. Set to `false` only if a valid ID is incorrectly rejected.
  verify_channels: true

  # DEBUG, INFO, WARNING, ERROR
  log_level: "INFO"

  # Validate the config and exit without starting the service.
  check_only: false

  # false, true, or a list of event names.
  # Also accepts environment-variable strings such as "upload,live".
  test_on_start: false

  # Optional rotating log file; relative paths are under data_dir.
  log_file: "${YTNOTI_LOG_FILE:-}"
  log_max_bytes: 5000000
  log_backup_count: 3

# Persistent application state and video history directory.
data_dir: "/data"

# Optional YouTube Data API v3 key for live/upcoming detection.
youtube_api_key: "${YOUTUBE_API_KEY:-}"

discord:
  # Optional default webhook display name. Overrides Discord webhook defaults.
  username: "YouTube Notifications"

  # Optional default webhook avatar URL. Overrides Discord webhook defaults.
  avatar_url: "https://example.com/avatar.png"

  # Discord allowed-mentions configuration.
  # Empty parse list prevents broad role/user/everyone parsing.
  # Use ID based mentions 
  # Supported values are here, everyone, role, & user.
  # <@658502865257496606> # user
  # <@&911408ID422069604413> # role
  # Must have Discord developer mode enabled to copy ID's
  allowed_mentions:
    parse: []

  # Number of delivery attempts permitted by the sender.
  max_retries: 5

# Named Discord webhook(s) and their URL's.
webhooks:
  main: "${DISCORD_WEBHOOK_URL}"
  alerts: "${DISCORD_ALERTS_WEBHOOK_URL:-}"

# Shared settings inherited by channels unless overridden.
defaults:
  webhooks:
    - main

  events:
    upload:
      enabled: true
      webhooks: [main]
      username: "YouTube Uploads"
      avatar_url: "https://example.com/uploads.png"
      content: "📺 **{channel_name}** uploaded a new video!"
      embed:
        title: "{title}"
        url: "{url}"
        description: "A new video is available."
        color: "#FF0000"
        author: true
        image: true
        thumbnail: false
        footer: "YouTube Notifications"
        timestamp: "published"
        fields:
          - name: "Channel"
            value: "[{channel_name}]({channel_url})"
            inline: true
          - name: "Video ID"
            value: "`{video_id}`"
            inline: true

    live:
      enabled: true
      webhooks: [main, alerts]
      username: "Live Notifications"
      content: "🔴 **{channel_name}** is live!"
      embed:
        title: "{title}"
        url: "{url}"
        description: "Watch the live stream now."
        color: "#E62117"
        author: true
        image: false
        thumbnail: true
        footer: "Live stream notification"
        timestamp: "now"
        fields:
          - name: "Watch"
            value: "[Open stream]({url})"
            inline: true

    upcoming:
      enabled: false
      webhooks: [main]
      content: "📅 **{channel_name}** scheduled a live stream."
      embed:
        title: "{title}"
        url: "{url}"
        description: "Starts <t:{scheduled_ts}:R>"
        color: "#3498DB"
        author: true
        image: false
        thumbnail: true
        footer: "Upcoming stream"
        timestamp: "now"
        fields:
          - name: "Scheduled time"
            value: "<t:{scheduled_ts}:F>"
            inline: true

    edit:
      enabled: true
      webhooks: [alerts]
      username: "Video Updates"
      content: "{change}"
      cooldown_seconds: 300
      only_if_title_changed: false
      show_diff: true
      embed:
        title: "✏️ Video updated: {title}"
        url: "{url}"
        description: "{change}"
        color: "#FFA500"
        author: true
        image: false
        thumbnail: true
        footer: "Video edit notification"
        timestamp: "updated"
        fields:
          - name: "Previous title"
            value: "{old_title}"
            inline: false
          - name: "Current title"
            value: "{title}"
            inline: false
          - name: "Video"
            value: "[Watch video]({url})"
            inline: true

    delete:
      enabled: true
      webhooks: [alerts]
      username: "Video Deletions"
      content: ""
      embed:
        title: "🗑️ Video deleted"
        url: ""
        description: "**{title}**\n`{video_id}`"
        color: "#808080"
        author: true
        image: false
        thumbnail: false
        footer: "Video deletion notification"
        timestamp: "now"
        fields:
          - name: "Channel"
            value: "{channel_name}"
            inline: true
          - name: "Channel ID"
            value: "`{channel_id}`"
            inline: true

# YouTube channels to monitor.
channels:
  - id: "UCuFFtHWoLl5fauMMD5Ww2jA"
    name: "CBC News"

    # Optional: overrides defaults.webhooks for this channel.
    webhooks: [main]

    # Optional per-channel event overrides.
    # These merge over defaults.events and built-in event settings.
    events:
      upload:
        content: "📺 New upload from **{channel_name}**: {title}"
        embed:
          color: "#00AAFF"

      live:
        webhooks: [alerts]
        content: "<@&123456789012345678> **{channel_name}** is LIVE: {title}"

      upcoming:
        enabled: true

      edit:
        cooldown_seconds: 600
        only_if_title_changed: false
        show_diff: true

      delete:
        enabled: false
```

</details>

#### Notes about the example

- Configure a real URL for every webhook destination you use. An empty webhook URL is not a valid destination.
- The `alerts` webhook must exist if any event routes to it.
- The channel ID is an example and should be replaced with the YouTube channel you want to monitor. Defaults to CBC News.
- `upcoming` is disabled in the built-in defaults in this reference; the per-channel override above enables it.
- `embed: false` can be used for plain-text notifications.
- Unknown configuration keys are rejected rather than silently ignored.

[🔼 Back to top](#configuration)

### Configuration keys

The following tables summarize the configuration keys and the types described by the application's schema.

#### Root-level keys

| Key | Type / requirement | Description |
| --- | --- | --- |
| `server` | Object; required | HTTP callback server settings. |
| `data_dir` | Path; default `/data` | Persistent state and video-history directory. |
| `youtube_api_key` | String or null; optional | YouTube Data API v3 key, used for improved live/upcoming detection. |
| `discord` | Object | Default Discord message and delivery settings. |
| `webhooks` | Mapping; at least one required | Named Discord webhook destinations. |
| `defaults` | Object | Settings inherited by channels and events. |
| `channels` | List; at least one required | YouTube channels to monitor. |

#### `server`

| Key | Type / accepted values | Description |
| --- | --- | --- |
| `callback_url` | Required URL string | Public callback URL used by the application. |
| `host` | String; default `0.0.0.0` | Address on which the HTTP server listens. |
| `port` | Integer from 1 to 65535; default `8000` | HTTP listening port. |
| `secret` | String or null | Optional shared secret for callback authentication. |
| `verify_channels` | Boolean; default `true` | Controls YouTube channel verification. |
| `log_level` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | Logging verbosity. |
| `check_only` | Boolean; default `false` | Validate configuration and exit without starting the service. |
| `test_on_start` | Boolean or list of event names | Enables startup test notifications. |
| `log_file` | String or null | Optional log-file path. Relative paths are under `data_dir`. |
| `log_max_bytes` | Integer ≥ 10000; default `5000000` | Maximum log-file size before rotation. |
| `log_backup_count` | Integer from 0 to 100; default `3` | Number of rotated log files to retain. |

#### `discord`

| Key | Type / accepted values | Description |
| --- | --- | --- |
| `username` | String or null | Default webhook display name. |
| `avatar_url` | String or null | Default webhook avatar URL. |
| `allowed_mentions` | Dictionary | Discord allowed-mentions payload settings. |
| `max_retries` | Integer from 1 to 10; default `5` | Maximum delivery attempts. |

For example, `allowed_mentions: {parse: []}` prevents broad parsing of everyone, user, and role mentions. Configure explicit mention allowances carefully.

#### `webhooks`

`webhooks` is a mapping of user-defined names to Discord webhook URLs:

```yaml
webhooks:
  main: "${DISCORD_WEBHOOK_URL}"
  alerts: "${DISCORD_ALERTS_WEBHOOK_URL}"
```

Use these names in `defaults.webhooks`, event-level `webhooks`, or channel-level `webhooks`. A valid webhook URL may also be used directly in a routing list if supported by the application's routing logic.

#### `defaults`

| Key | Type | Description |
| --- | --- | --- |
| `defaults.webhooks` | List of destinations | Default destinations for notifications. |
| `defaults.events` | Mapping of event names to event settings | Shared event configuration. |

#### `channels[]`

Each channel entry describes one YouTube channel to monitor.

| Key | Type / requirement | Description |
| --- | --- | --- |
| `id` | Required YouTube channel ID | Must match the `UC` plus 22-character ID pattern; handles are not accepted. |
| `name` | String; optional | Display name for the channel. |
| `webhooks` | List of destinations; optional | Overrides the channel's default routing destinations. |
| `events` | Mapping; optional | Per-channel event overrides. |

Example:

```yaml
channels:
  - id: "UCxxxxxxxxxxxxxxxxxxxxxx"
    name: "Example Channel"
    webhooks: [main]
    events:
      upload:
        enabled: true
```

Replace the placeholder with a real channel ID.

[🔼 Back to top](#configuration)

### Supported event types

The five event names supported by the schema are:

| Event | Built-in default | Purpose |
| --- | --- | --- |
| `upload` | Enabled | Notify when a new video is uploaded. |
| `live` | Enabled | Notify when a channel goes live. |
| `upcoming` | Disabled | Notify about an upcoming scheduled stream. |
| `edit` | Enabled | Notify when video metadata changes. |
| `delete` | Enabled | Notify when a video is deleted. |

#### Event settings

These settings are accepted by the event configuration model:

| Key | Type / default | Description |
| --- | --- | --- |
| `enabled` | Boolean; default `true` | Enable or disable this event. |
| `webhooks` | List of destinations or null | Destinations for this event. |
| `username` | String or null | Webhook username override. |
| `avatar_url` | String or null | Webhook avatar override. |
| `content` | String; default empty | Discord message content. |
| `embed` | Embed object or `false` | Embed configuration, or disable embeds for plain text. |
| `cooldown_seconds` | Non-negative integer; schema default `0` | Cooldown for repeated edit notifications. |
| `only_if_title_changed` | Boolean; default `false` | Restrict edit notifications to title changes. |
| `show_diff` | Boolean; default `true` | Include a summary of changes in edit notifications. |

The final three settings are intended for edit notifications. Although the event model may accept them on other event types, configure them under `edit` for clarity.

#### Embed settings

| Key | Type / accepted values | Description |
| --- | --- | --- |
| `title` | String | Embed title. |
| `url` | String | URL associated with the embed title. |
| `description` | String | Embed description. |
| `color` | Integer or hexadecimal string | Embed accent color, for example `"#FF0000"`. |
| `author` | Boolean | Whether to include the channel as the embed author. |
| `image` | Boolean | Whether to include the video image. |
| `thumbnail` | Boolean | Whether to include the thumbnail. |
| `footer` | String | Embed footer text. |
| `timestamp` | `published`, `updated`, `now`, `none` | Selects the embed timestamp source. |
| `fields` | List of field objects | Additional embed fields. |

Each embed field supports:

| Key | Type / requirement | Description |
| --- | --- | --- |
| `name` | Required string | Field label. |
| `value` | Required string | Field content. |
| `inline` | Boolean; default `false` | Whether Discord may display the field inline. |

Example:

```yaml
embed:
  title: "{title}"
  url: "{url}"
  description: "New video from {channel_name}"
  color: "#FF0000"
  author: true
  image: true
  thumbnail: false
  footer: "YouTube Notifications"
  timestamp: "published"
  fields:
    - name: "Channel"
      value: "[{channel_name}]({channel_url})"
      inline: true
```

Set `embed: false` on an event to send a plain-text notification instead of an embed.

[🔼 Back to top](#configuration)

### Message-template variables

Use the following variable names in message content and supported embed text fields, such as `title`, `url`, `description`, `footer`, and field `name` or `value`.

| Variable | Meaning |
| --- | --- |
| `{event}` | Event name: `upload`, `live`, `upcoming`, `edit`, or `delete`. |
| `{title}` | Current video title. |
| `{old_title}` | Previous title, primarily useful for edit events. |
| `{change}` | Summary of detected changes. |
| `{video_id}` | YouTube video ID. |
| `{url}` | YouTube video URL. |
| `{thumbnail}` | Video thumbnail URL. |
| `{channel_id}` | YouTube channel ID. |
| `{channel_name}` | Channel display name. |
| `{channel_url}` | YouTube channel URL. |
| `{published}` | Publication timestamp. |
| `{updated}` | Update timestamp. |
| `{published_ts}` | Publication Unix timestamp in seconds. |
| `{updated_ts}` | Update Unix timestamp in seconds. |
| `{scheduled_ts}` | Scheduled-stream Unix timestamp in seconds. |
| `{deleted_ts}` | Deletion Unix timestamp in seconds. |

#### Template formatting rules

- Use Python-style brace formatting, such as `{title}`; do not use `${title}`.
- Use `{{` and `}}` to display literal braces.
- An unknown variable can cause configuration validation to fail.
- A variable may be valid but not meaningful for every event. For example, `{scheduled_ts}` is intended for scheduled-stream notifications.
- Unix timestamps can be used with Discord timestamp markup. For example: `<t:{scheduled_ts}:R>`.

[🔼 Back to top](#configuration)

### Environment-variable interpolation

The configuration loader supports environment-variable interpolation in YAML strings.

#### Required variable

If the variable is missing or empty, validation fails:

```yaml
callback_url: "${CALLBACK_URL}"
```

#### Optional variable

Use `:-` to provide a fallback when the variable is missing or empty:

```yaml
youtube_api_key: "${YOUTUBE_API_KEY:-}"
server:
  log_level: "${LOG_LEVEL:-INFO}"
```

Environment-variable names start with a letter or underscore and can contain letters, digits, and underscores. The variables are not restricted to a predefined list: choose the names you want to use in YAML and provide them in the application environment.

Common examples include:

```yaml
server:
  secret: "${YTNOTI_SECRET:-}"
  log_file: "${YTNOTI_LOG_FILE:-}"

youtube_api_key: "${YOUTUBE_API_KEY:-}"

webhooks:
  main: "${DISCORD_WEBHOOK_URL}"
  alerts: "${DISCORD_ALERTS_WEBHOOK_URL:-}"
```

[🔼 Back to top](#configuration)

### Override and merge behavior

Configuration is layered in this order:

1. Built-in event defaults.
2. `defaults.events` overrides.
3. `channels[].events` overrides.

Later settings override earlier values for the same key. Nested embed objects are merged recursively, so a channel can override only `embed.color` without restating the entire embed configuration.

Webhook routing is resolved separately. Event-specific destinations take precedence over channel and global defaults.

For example:

```yaml
defaults:
  events:
    upload:
      enabled: true
      webhooks: [main]
      embed:
        color: "#FF0000"

channels:
  - id: "UCxxxxxxxxxxxxxxxxxxxxxx"
    events:
      upload:
        embed:
          color: "#00AAFF"
```

In this example, the channel inherits the default upload settings but uses its own embed color.

[🔼 Back to top](#configuration)

---

## Validation

The configuration schema is strict: unsupported or misspelled keys are rejected instead of silently ignored. Validation also checks items such as:

- YAML parsing and configuration structure.
- Required values and accepted types.
- Webhook URL formats.
- YouTube channel ID formats.
- Supported event names.
- Template variable names.

To validate the file without starting the long-running service, run the application's configuration-check command:

```bash
python -m ytnoti_discord -c config.yaml --check
```

Check the command-line options for the exact version you have installed if the invocation differs.

[🔼 Back to top](#configuration)

## Live and upcoming detection

The underlying `ytnoti` push feed does not by itself distinguish normal videos from streams. The service checks stream state on each push:

- With `youtube_api_key`, it uses the official YouTube Data API (one quota unit per push).
- Without a key, it parses the public watch page. This avoids API quota use but can fail if YouTube changes the page or blocks requests.

A scheduled stream is normally silent until it goes live, then one `live` notification is sent. Enable `upcoming` to announce scheduled streams. The end-of-stream feed update is not reported as an edit.

[🔼 Back to top](#configuration)

## State, logging, and delivery

- Persistent state is stored under `/data` in Docker. Keep this volume to preserve edit snapshots and event state across restarts.
- Edit comparisons use saved snapshots. If a video has no earlier snapshot, the first edit may not have a complete before/after diff.
- File logging is optional; console logging remains enabled. Log files rotate at `log_max_bytes` and retain `log_backup_count` backups.
- Webhook URLs and API keys are redacted from log files.
- Discord delivery is retried when appropriate, including rate limits. Failed messages are eventually dropped rather than queued indefinitely.

[🔼 Back to top](#configuration)

## Command-line options

Run the CLI inside the project/container with `python -m ytnoti_discord`.

| Option | Purpose |
| --- | --- |
| `-c`, `--config PATH` | Use a specific YAML configuration file. |
| `--check` | Validate the configuration, print a summary, and exit. |
| `--test [EVENT ...]` | Send sample notifications. With no event names, tests all event types. |
| `--channel CHANNEL_ID` | Restrict test notifications to a specified channel ID. |
| `--help` | Show CLI help. |

Examples:

```bash
docker compose run --rm ytnoti-discord --check
docker compose run --rm ytnoti-discord --test
docker compose run --rm ytnoti-discord --test live upcoming
docker compose run --rm ytnoti-discord --test --channel UCxxxxxxxxxxxxxxxxxxxxxx
```

Test notifications use your configured webhooks and templates but are clearly labelled and never ping users or roles by default. The command exits non-zero if any delivery fails.

[🔼 Back to top](#configuration)
