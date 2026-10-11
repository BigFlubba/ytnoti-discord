# ytnoti-discord

A self-hosted Docker container that posts **Discord webhook messages when a YouTube
channel uploads, edits, deletes a video, or goes live**, driven by one YAML file.
It wraps the [`ytnoti`](https://github.com/SeoulSKY/ytnoti) library, which receives
YouTube's push notifications (WebSub/PubSubHubbub): **no polling, no API key, no quota**.

| Event | When it fires |
|---|---|
| `upload` | A new video appears |
| `live` | A live stream starts (sent once per stream) |
| `upcoming` | A live stream is scheduled (**off by default**) |
| `edit` | A video's title, description or tags change (shows a *before → after*) |
| `delete` | A video is removed |

## Quick start

```bash
cp config.example.yaml config.yaml     # set callback_url and your channel IDs
cp .env.example .env                   # put your Discord webhook URL in here
docker compose run --rm ytnoti-discord --check   # validate config
docker compose run --rm ytnoti-discord --test    # send a sample of EVERY event to Discord
docker compose up -d --build
docker compose logs -f
```

You need **(1)** a Discord webhook URL (Channel settings → Integrations → Webhooks),
**(2)** YouTube channel IDs (`UC…`, 24 chars; `@handles` are not accepted), and
**(3)** a public HTTPS URL that forwards to the container's port 8000 (next section).

## Choosing the config file and check mode

The project's own `config.yaml` is used by default. Lookup order: `-c/--config`, then
`$CONFIG_PATH` (the Docker image sets it to `/config/config.yaml`), then `./config.yaml`
in the current directory, then `config.yaml` in the project folder, and finally
`/config/config.yaml`. `--check` prints which file was loaded.

`--check` can also be driven from the file: set `server.check_only: true` to validate
the config, print the summary and exit without starting the service (the container then
exits with code 0, or 2 on a config error). Set it back to `false` to run normally. An
explicit `--test` flag still works while it is set.

## The public callback URL (the one non-obvious requirement)

YouTube's hub *calls you*, so `server.callback_url` must be reachable from the internet.
Put a reverse proxy or tunnel in front of port 8000:

```
# Caddy (automatic HTTPS)
yt.example.com {
    reverse_proxy localhost:8000
}
```
Cloudflare Tunnel or nginx work the same way: forward the callback URL's path to
`http://<container>:8000`. Then set `callback_url: https://yt.example.com/ytnoti`
(any path works, including `/`). Prefer HTTPS.

The callback endpoint is hardened beyond the library defaults: hub verification is
answered only for your configured channels (unknown topics get 404, malformed
parameters 400, always as `text/plain`), pushes need a well-formed `X-Hub-Signature`
that matches the HMAC (otherwise 401), bodies over 1 MiB get 413, and unexpected errors
return empty 4xx/5xx responses instead of tracebacks. The `Server` header is omitted.
Put rate limiting in your reverse proxy if the URL is exposed to untrusted traffic.

**On startup the container requests its own callback URL** and needs HTTP 200
(no redirects) before it subscribes. `/healthz` and the Docker health status only turn
healthy once that works.

## Configuration

`config.example.yaml` documents every option. Highlights:

* **Secrets via environment:** `${DISCORD_WEBHOOK_URL}` or `${VAR:-default}` anywhere.
* **Routing:** several named `webhooks`; set `webhooks:` per channel or per event
  (e.g. live pings to `#alerts`, uploads to `#videos`).
* **Messages:** each event has `content` plus an `embed` (or `embed: false` for text
  only, which lets Discord show its inline YouTube player). Templates support
  `{title} {old_title} {change} {url} {channel_name} {published_ts} {scheduled_ts} …`.
  Typos in variable names fail at startup, not at 3 a.m.
* **Edit before/after:** edit messages show what changed: the title (old → new), the
  description (only the changed lines, as a `-` before / `+` after diff) and tags
  (removed / added). Set `show_diff: false` on `edit` for the old one-line message.
  See "How edits are compared" below.
* **Edit noise control:** `cooldown_seconds` (default 300) and `only_if_title_changed`.
* **Mentions:** `<@&roleid>` works in `content`. `@everyone`/`@here` are blocked unless
  you add `everyone` to `discord.allowed_mentions.parse`. Video titles are
  de-fanged in `content` so a title can never ping anyone.

## How edits are compared

YouTube's push feed only contains the title, so the description and tags come from the
same lookup used for live detection (the Data API if `youtube_api_key` is set, else the
watch page), at no extra quota or requests. The service saves a snapshot of each video's
title, description and tags in `/data/state.json` every time it sees the video, and the
next edit is compared against it.

* A video first seen **before this feature existed**, or whose details couldn't be read,
  has no "before" yet: that edit falls back to the plain message (plus the title change)
  and the snapshot is recorded, so the *following* edit gets a full diff. A diff is never
  invented from unknown data.
* The snapshot updates on every push, even when the notification is skipped (cooldown,
  `only_if_title_changed`), so a diff shows what changed since the last push.
* Thumbnails, privacy and other settings aren't in the feed or compared. Such edits
  show "The video details were updated."
* Long descriptions show only the changed lines and are truncated. State grows with
  the stored descriptions (up to 2000 videos are kept).
* YouTube's API can briefly serve a cached copy right after an edit; if so the change
  appears with the next push.

## How "live" detection works (and its limits)

`ytnoti` can't tell a live stream from a normal video: it reports "upload" the first
time a video appears and "edit" afterwards. This container therefore asks YouTube for
the video's live state on each push:

* **With `youtube_api_key`** (recommended): official Data API, 1 quota unit per push.
* **Without a key:** it reads the public watch page. This costs no quota but depends on
  YouTube's page format, which can change or be blocked/consent-walled from some
  hosts. If detection fails you'll see a warning, and the event falls back to being
  treated as a normal upload/edit.

Resulting behavior: a scheduled stream is **silent** until it starts (then one `live`
message), unless you enable `upcoming`. The end-of-stream feed update is not reported
as an edit.

## Troubleshooting

* **"Callback URL test failed, retrying…" / unhealthy:** the container can't fetch its
  own public URL. Check DNS, the proxy target, the certificate, and that the URL
  doesn't redirect. On a home network without NAT hairpinning, map your domain to
  the host in `docker-compose.yml` (`extra_hosts: ["yt.example.com:host-gateway"]`).
* **"Failed to subscribe … might be inaccessible from the public internet":** YouTube's
  hub could not reach your URL. Test from outside your network.
* **"Invalid channel ID" for a valid ID:** set `server.verify_channels: false`.
* **Discord 404/401 in logs:** the webhook was deleted or the URL is wrong.
* **No state across restarts:** make sure `/data` is a volume. With a bind mount,
  `chown 10001:10001` the directory (the container runs unprivileged).
* **Silence the logs?** `server.log_level: WARNING`. Webhook URLs are never logged.
* **Keep logs in a file?** Set `server.log_file` (relative paths live under `data_dir`,
  so `logs/ytnoti-discord.log` ends up in `/data/logs/` in Docker). The file rotates at
  `log_max_bytes` (5 MB) keeping `log_backup_count` (3) old files, and includes the
  web server's own messages. Webhook URLs and API keys are redacted from it. Only the
  running service writes it (not `--check`/`--test`). If the file can't be opened the
  service warns and keeps logging to the console. With a bind-mounted `/data`, the
  directory must be writable by UID 10001.

## Notes

* Subscriptions are renewed automatically (the library refreshes before the lease
  expires) and are re-requested on every start.
* Deliveries are acknowledged immediately and sent in the background; if Discord is
  unreachable the send is retried (honoring rate limits) and then dropped, not queued.
* Run as non-root, read-only filesystem, no capabilities (see `docker-compose.yml`).
* Edits made only to a video's description show as "details were updated" because the
  push feed doesn't include the description.

## Continuous integration and releases

GitHub Actions runs on pushes to `main`/`master`, pull requests, and manual
workflow dispatches. CI tests the Python code on Python 3.12 and 3.13, compiles the
application and tests, builds the Docker image, and runs a container CLI smoke test.
Pull requests must pass these checks before they are merged (configure the repository's
branch protection rules to require the **Python tests** and **Build and smoke-test Docker
image** checks).

Releases are managed by [Release Please](https://github.com/googleapis/release-please):
use [Conventional Commit](https://www.conventionalcommits.org/) messages such as
`fix: handle YouTube feed retries` or `feat: add event filter`. On pushes to the default
branch, it opens or updates a release PR containing the version and changelog updates.
Merge that PR to create a GitHub release; the workflow then builds and publishes the
container to GitHub Container Registry (GHCR) with `latest`, the version, and the release
tag as tags. The resulting image is `ghcr.io/OWNER/REPOSITORY:latest` (replace `OWNER`
and `REPOSITORY` with the GitHub repository path). The repository's Actions token needs
permission to write packages; the workflow requests this for the publishing job.

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest tests -q
python -m ytnoti_discord -c config.yaml --check
```
`ytnoti` is MIT-licensed by SeoulSKY.
