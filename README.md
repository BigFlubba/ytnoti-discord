<div align="center">

# ytnoti-discord

</div>

<div align="center">

[![](https://img.shields.io/github/v/release/BigFlubba/ytnoti-discord?style=for-the-badge&color=purple)](https://github.com/BigFlubba/ytnoti-discord/releases)
[![](https://img.shields.io/github/actions/workflow/status/BigFlubba/ytnoti-discord/release-please.yml?style=for-the-badge)](https://github.com/BigFlubba/ytnoti-discord/actions/workflows/release-please.yml)
[![](https://img.shields.io/github/license/BigFlubba/ytnoti-discord?style=for-the-badge&color=ee512b)](LICENSE)

</div>

Highly configurable self-hosted YouTube notifications for Discord powered by a yaml config file. The Docker service uses YouTube push notifications (WebSub) and wraps the [`ytnoti`](https://github.com/SeoulSKY/ytnoti) library to report uploads, live streams, scheduled streams, edits, and deletions without continuously polling YouTube.

---

## Table of Contents

- [ytnoti-discord](#ytnoti-discord)
  - [Table of Contents](#table-of-contents)
  - [Features](#features)
  - [Quick start](#quick-start)
    - [Requirements](#requirements)
    - [1. Create your files](#1-create-your-files)
    - [2. Create your Discord webhook](#2-create-your-discord-webhook)
    - [3. Set the required variables](#3-set-the-required-variables)
    - [4. Make the callback URL reachable](#4-make-the-callback-url-reachable)
    - [5. Run with Docker Compose](#5-run-with-docker-compose)
    - [6. Validate and test](#6-validate-and-test)
    - [7. Start the service](#7-start-the-service)
  - [Configuration options](#configuration-options)
  - [Troubleshooting](#troubleshooting)
  - [Development](#development)
  - [AI Disclosure](#ai-disclosure)
  - [License](#license)
  - [Star History](#star-history)

## Features

- Upload, live, upcoming, edit, and delete notifications.
- Multiple Discord webhooks with global, channel, and event-level routing.
- Custom message templates and Discord embeds.
- Edit summaries with title changes, description diffs, and tag changes.
- Persistent state and configurable logging.
- No YouTube API key is required for basic notifications. A key improves live/upcoming detection.

## Quick start

### Requirements

- Docker & Docker Compose.
- A Discord webhook URL.
- One or more YouTube channel IDs (`UC…`, not an `@handle`).
- A public HTTPS URL that forwards to the container's port `8000` by default.

### 1. Create your files

```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

### 2. Create your Discord webhook

Create a Discord webhook in your Discord channel under **Edit Channel → Integrations → Webhooks**.

### 3. Set the required variables

1. Edit your `.env` file and set all the required variables.
2. Edit your `config.yaml` file and replace the example channel ID under `channels` with the YouTube channel ID you want to recieve notifications from.

### 4. Make the callback URL reachable

> [!NOTE]
> Port forwarding without any protections is not recommended. Use a reverse proxy with rate limiting and the ability to filter IP addresses.
>
> For an easier setup you can use a service like Cloudflare Tunnels to prevent opening ports and to mask your real IP address.

YouTube's notification hub must be able to reach `server.callback_url`. Configure a reverse proxy or tunnel to forward that URL to the container on port `8000` or your custom port. For example, with Caddy:

```caddy
yt.example.com {
    reverse_proxy localhost:8000
}
```

Use your actual domain and ensure HTTPS is working. The service checks its callback URL on startup; redirects or an unreachable URL prevent it from becoming healthy.

### 5. Run with Docker Compose

```yml
services:
  ytnoti-discord:
    image: ghcr.io/bigflubba/ytnoti-discord:latest
    container_name: ytnoti-discord
    restart: unless-stopped
    ports:
      # Behind a reverse proxy on the same host, bind to loopback instead:
      # - "127.0.0.1:8000:8000"
      - "8000:8000"
    env_file:
      - .env
    volumes:
      - ./config.yaml:/config/config.yaml:ro
      - ytnoti-data:/data
    read_only: true
    tmpfs:
      - /tmp
    cap_drop: [ALL]
    security_opt:
      - no-new-privileges:true
    # If the container cannot reach its own public URL (home server without
    # NAT hairpinning), map your domain to the host. See README > Troubleshooting.
    # extra_hosts:
    #   - "yt.example.com:host-gateway"

volumes:
  ytnoti-data:

```

### 6. Validate and test

- Check `config.yaml` for errors
  - <details>
      <summary>Via `config.yaml` (click to open)</summary>

    1. Edit `config.yaml`
    2. Set `server.check_only` to `true`
    3. Save `config.yaml`
    4. Start the service
    5. Check the logs for any errors by running

        ```bash
        docker logs -f ytnoti-discord
        ```

    </details>

  - <details>
      <summary>Via the command line (click to open)</summary>

    1. Run

        ```bash
        docker run --rm ytnoti-discord --check
        ```

    2. Check the logs for any errors

    </details>

- Test notifications
  - <details>
      <summary>Via `config.yaml` (click to open)</summary>

    1. Edit `config.yaml`
    2. Set `server.test_on_start` to `true`
    3. Save `config.yaml`
    4. Start the service
    5. Check you can recieve notifications by running

        ```bash
        docker logs -f ytnoti-discord
        ```

    6. Check Discord for notifications
    7. Once the notifications are correct edit `config.yaml`
    8. Set `server.test_on_start` to `false` or remove the line
    9. Save `config.yaml`
    10. Restart the service

    </details>

  - <details>
      <summary>Via the command line (click to open)</summary>

    1. Run

        ```bash
        docker run --rm ytnoti-discord --test
        ```

    2. Check Discord for notifications

    </details>

The check command validates your `config.yaml` file for errors.

The test command sends sample messages for all event types to your configured webhook(s) every time the service starts. Test messages are labelled and do not ping users or roles by default. To let test messages ping (following `discord.allowed_mentions`), set `server.test_allowed_mentions: true`. You can test selected events via:

### 7. Start the service

```bash
docker compose up -d
```

Stop it with `docker compose down`. Keep the `/data` volume so notification state survives container recreation.

## Configuration options

For full environment-variable and YAML documentation, see [CONFIGURATION.md](docs/CONFIGURATION.md).

## Troubleshooting

See [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for details

## Development

```bash
python -m pip install -r requirements.txt pytest
python -m compileall -q ytnoti_discord tests
python -m pytest -q
python -m ytnoti_discord -c config.yaml --check
```

## AI Disclosure

AI was used in the making of this project. Commits are reviewed by a human before being published.

## License

The `ytnoti` library is MIT-licensed by SeoulSKY.

## Star History

<a href="https://www.star-history.com/?repos=bigflubba%2Fytnoti-discord&type=date&legend=bottom-right">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=bigflubba/ytnoti-discord&type=date&theme=dark&legend=bottom-right" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=bigflubba/ytnoti-discord&type=date&legend=bottom-right" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=bigflubba/ytnoti-discord&type=date&legend=bottom-right" />
 </picture>
</a>