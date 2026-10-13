# Troubleshooting

## Table of Contents

- [Troubleshooting](#troubleshooting)
  - [Table of Contents](#table-of-contents)
  - [Callback check fails / container is unhealthy](#callback-check-fails--container-is-unhealthy)
  - [Subscription fails](#subscription-fails)
  - [A valid channel ID is rejected](#a-valid-channel-id-is-rejected)
  - [Discord returns 401 or 404](#discord-returns-401-or-404)
  - [State is lost after recreation](#state-is-lost-after-recreation)
  - [Live detection is unreliable](#live-detection-is-unreliable)
  - [Unknown configuration key](#unknown-configuration-key)
  - [Invalid webhook URL](#invalid-webhook-url)
  - [Invalid channel ID](#invalid-channel-id)
  - [Unknown template variable](#unknown-template-variable)
  - [Upcoming notifications are missing](#upcoming-notifications-are-missing)
  - [Role mentions do not work](#role-mentions-do-not-work)

## Callback check fails / container is unhealthy

Verify DNS, HTTPS certificate, reverse-proxy target, and that the callback URL returns HTTP 200 without redirecting. The host must also be able to reach its own public URL.

## Subscription fails

Test the callback URL from outside your network and confirm that YouTube can reach it.

## A valid channel ID is rejected

Try `server.verify_channels: false`.

## Discord returns 401 or 404

Check that the webhook URL is correct and that the webhook still exists.

## State is lost after recreation

Ensure `/data` is backed by a persistent Docker volume. For a host bind mount, ensure it is writable by UID `10001`.

## Live detection is unreliable

Set `YOUTUBE_API_KEY` in `.env` and configure `youtube_api_key: ${YOUTUBE_API_KEY:-}` in `config.yaml`. Without a key, detection depends on YouTube's public watch page.

## Unknown configuration key

Check the spelling and nesting of the key. For example, `callback_url` belongs under `server`, while `data_dir` is a root-level key.

## Invalid webhook URL

Ensure each configured destination is a valid Discord webhook URL. Do not leave an empty webhook value if an event routes to it.

## Invalid channel ID

Use a YouTube channel ID, not a channel handle or display name. Channel IDs typically begin with `UC` and are 24 characters long.

## Unknown template variable

Use only the variable names listed in [CONFIGURATION.md#Message-template variables](configuration#message-template-variables). Check braces and spelling carefully.

## Upcoming notifications are missing

The `upcoming` event is disabled by default in this reference. Enable it in `defaults.events.upcoming.enabled` or for the individual channel.

## Role mentions do not work

Discord mention behavior is affected by `discord.allowed_mentions` and the permissions/configuration associated with the target role. Allow only the mentions you intend the bot to send.
