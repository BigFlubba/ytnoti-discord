# ytnoti-discord

Self-hosted YouTube notifications for Discord. The Docker service uses YouTube push notifications (WebSub) and wraps the [`ytnoti`](https://github.com/SeoulSKY/ytnoti) library to report uploads, live streams, scheduled streams, edits, and deletions without continuously polling YouTube.

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
- A public HTTPS URL that forwards to the container's port `8000`.
- Some networking knoladge.

### 1. Create your files

```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

Edit `.env` and set `DISCORD_WEBHOOK_URL` to your Discord webhook URL. Create a webhook in your Discord channel under **Edit Channel → Integrations → Webhooks**.

Edit `config.yaml`:
- Set `server.callback_url` to the public HTTPS URL that reaches this service, such as `https://yt.example.com/ytnoti`.
- Replace the example channel ID under `channels` with the YouTube channel ID you want to follow.

### 2. Make the callback URL reachable

YouTube's notification hub must be able to reach `server.callback_url`. Configure a reverse proxy or tunnel to forward that URL to the container on port `8000`. For example, with Caddy:

```caddy
yt.example.com {
    reverse_proxy localhost:8000
}
```

Use your actual domain and ensure HTTPS is working. The service checks its callback URL on startup; redirects or an unreachable URL prevent it from becoming healthy.

### 3. Run with Docker Compose

```yml
services:
  ytnoti-discord:
    image: ghcr.io/bigflubba/ytnoti-discord:latest
    container_name: ytnoti-discord
    restart: unless-stopped
    ports:
      # Behind a reverse proxy on the same host, bind to loopback instead:
      #   - "127.0.0.1:8000:8000"
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

### 4. Validate and test

Edit `config.yaml`:
- Set `server.check_only` to `true`
- Set `server.test_on_start` to `true`

Or

```bash
docker compose run --rm ytnoti-discord --check
docker compose run --rm ytnoti-discord --test
```

The check command validates your `config.yaml` file for errors.

The test command sends sample messages for all event types to your configured webhook(s) every time the service starts. Test messages are labelled and do not ping users or roles. You can test selected events via:

Edit `.env`:
- Set `server.test_on_start` to `live,upcoming`
- Once done set `server.test_on_start` to `false` or comment out the line

Or

```bash
docker compose run --rm ytnoti-discord --test live upcoming
```

### 5. Start the service

```bash
docker compose up -d
docker compose logs -f ytnoti-discord
```

Stop it with `docker compose down`. Keep the `/data` volume so notification state survives container recreation.

## Events

| Event | Meaning | Default |
|---|---|---|
| `upload` | A new video appears | Enabled |
| `live` | A live stream starts; sent once per stream | Enabled |
| `upcoming` | A scheduled stream is announced | Disabled |
| `edit` | Video title, description, or tags change | Enabled |
| `delete` | A video is removed | Enabled |

For full environment-variable and YAML documentation, see [Configuration reference](docs/CONFIGURATION.md). The example file [`config.example.yaml`](config.example.yaml) also contains inline comments.

## Configuration essentials

- Environment variables can be interpolated in YAML using `${NAME}` or `${NAME:-default}`.
- `webhooks` defines named Discord webhook URLs. Choose destinations globally, per channel, or per event.
- Each event can customize `content`, `username`, `avatar_url`, and `embed`. Use `embed: false` to send plain text.
- Template variables include `{title}`, `{old_title}`, `{change}`, `{url}`, `{video_id}`, `{channel_name}`, `{channel_id}`, `{channel_url}`, `{published}`, `{updated}`, `{published_ts}`, `{updated_ts}`, `{scheduled_ts}`, and `{deleted_ts}`.
- Role mentions can be used in message content. `@everyone` and `@here` are blocked unless explicitly enabled in `discord.allowed_mentions`.

## Troubleshooting

- **Callback check fails / container is unhealthy:** verify DNS, HTTPS certificate, reverse-proxy target, and that the callback URL returns HTTP 200 without redirecting. The host must also be able to reach its own public URL.
- **Subscription fails:** test the callback URL from outside your network and confirm that YouTube can reach it.
- **A valid channel ID is rejected:** try `server.verify_channels: false`.
- **Discord returns 401 or 404:** check that the webhook URL is correct and that the webhook still exists.
- **State is lost after recreation:** ensure `/data` is backed by a persistent Docker volume. For a host bind mount, ensure it is writable by UID `10001`.
- **Live detection is unreliable:** set `YOUTUBE_API_KEY` in `.env` and configure `youtube_api_key: ${YOUTUBE_API_KEY:-}` in `config.yaml`. Without a key, detection depends on YouTube's public watch page.

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
