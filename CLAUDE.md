# CLAUDE.md — ytnoti-discord

## Project purpose

This repository packages [`ytnoti`](https://github.com/SeoulSKY/ytnoti) in a self-hosted Docker service and sends Discord webhook notifications for YouTube channel events. Configuration is YAML-based. The application receives YouTube WebSub/PubSubHubbub push notifications, enriches events when possible to distinguish live/upcoming streams, renders Discord messages, and persists state under `/data`.

When working in this repository, prefer small, targeted changes that preserve the existing behavior and configuration format unless the task explicitly asks for a change.

## Repository map

- `ytnoti_discord/main.py` — application entry point, CLI options, server lifecycle.
- `ytnoti_discord/config.py` — YAML loading, environment-variable interpolation, validation, defaults.
- `ytnoti_discord/youtube.py` — YouTube event/live-state handling and related HTTP/API interactions.
- `ytnoti_discord/engine.py` — event processing, routing, deduplication/cooldowns, background delivery flow.
- `ytnoti_discord/render.py` and `templates.py` — notification/template rendering and Discord payload content.
- `ytnoti_discord/discord_client.py` — Discord webhook delivery, retry/rate-limit behavior.
- `ytnoti_discord/state.py` — persistent state management.
- `tests/` — pytest tests for configuration, rendering, event processing, YouTube behavior, Discord delivery, and end-to-end flows.
- `config.example.yaml` — documented example configuration; keep it aligned with configuration behavior.
- `.env.example` — names/placeholders for environment variables; never put real secrets here.
- `Dockerfile`, `docker-compose.yml` — container packaging and runtime defaults.
- `.github/workflows/ci.yml` — test, compile, Docker build, and smoke-test checks.
- `.github/workflows/release-please.yml` — Release Please PR automation and GHCR image publishing.
- `release-please-config.json`, `.release-please-manifest.json` — release configuration and version state.

## Development setup and checks

Use Python 3.12 as the primary local/runtime baseline. CI also tests Python 3.13.

```bash
python -m pip install -r requirements.txt pytest
python -m compileall -q ytnoti_discord tests
python -m pytest -q
```

Build and smoke-test the container when Docker is available:

```bash
docker build -t ytnoti-discord:local .
docker run --rm ytnoti-discord:local --help
```

For configuration validation, use a local config file and the CLI's check mode:

```bash
python -m ytnoti_discord -c config.yaml --check
```

Do not claim tests, Docker builds, or live integrations passed unless they were actually run and their results were observed. Network-dependent checks may be unavailable in a restricted environment; state that limitation clearly.

## Change and testing guidelines

1. Read the relevant implementation and tests before changing behavior.
2. Add or update tests for bug fixes and new behavior. Prefer deterministic unit tests with mocked HTTP/API calls and mocked Discord webhook delivery; tests must not contact real YouTube endpoints or Discord unless explicitly designed as opt-in integration tests.
3. Run the narrowest relevant tests first, then the full test suite and compile check where feasible.
4. Keep configuration defaults and validation explicit. If adding or changing a config option, update `config.example.yaml`, tests, and README documentation as appropriate.
5. Preserve backward compatibility for existing YAML configuration and Docker Compose usage unless the request explicitly calls for a breaking change.
6. Avoid introducing dependencies unless necessary. If a dependency is required, update `requirements.txt` and add coverage for its use.
7. Keep logs useful for operators, but never log webhook URLs, API keys, tokens, or other credentials.
8. Keep event processing resilient: account for duplicate push notifications, retries, rate limits, malformed upstream responses, and unavailable external services.
9. Do not suppress test failures or weaken checks just to make CI green.

## Security and runtime expectations

- Treat Discord webhook URLs, YouTube API keys, and all environment values as secrets.
- Never add real credentials to source, examples, tests, logs, workflow files, or commits. Use placeholders and test fixtures.
- Keep the container running as the existing unprivileged `app` user (UID 10001) unless a justified change is requested.
- Preserve `/data` as persistent writable state and `/config/config.yaml` as the default configuration path unless the application contract is deliberately changed.
- Avoid adding Linux capabilities or privileged container settings. Respect the security posture in `docker-compose.yml`.
- Do not make network calls in tests by default. Mock external dependencies.
- Validate and escape untrusted video/channel text before placing it into Discord content. Do not accidentally enable `@everyone` or `@here` mentions.

## GitHub Actions, Docker, and releases

- CI runs on pushes to `main`/`master`, pull requests, and manual dispatch. Keep test and Docker smoke-test checks meaningful and required checks stable where possible.
- Release automation uses Release Please and Conventional Commits. Use commit subjects such as `fix: handle malformed feed data`, `feat: support an event option`, or `docs: clarify reverse proxy setup`.
- Breaking changes should use `!` and/or a `BREAKING CHANGE:` footer so Release Please can calculate the appropriate major version.
- The release workflow publishes to GitHub Container Registry. Keep the image name consistent with the existing package: `ghcr.io/${OWNER}/${REPOSITORY}`.
- Release images should carry the immutable version tag and the `latest` tag. Do not publish images from ordinary pull requests or un-released commits unless specifically requested.
- Any workflow permission changes should follow least privilege. Publishing needs `packages: write`; routine CI should not receive unnecessary write permissions.
- Pin or update GitHub Actions versions deliberately, and preserve build caching where it is useful.
- Do not change Release Please's manifest/config or image naming without checking the resulting version/tag and publishing behavior.

## Documentation

Keep `README.md`, `config.example.yaml`, `.env.example`, and Docker Compose settings consistent with actual behavior. Prefer concrete commands and document requirements such as the publicly reachable HTTPS callback URL. Clearly distinguish tested behavior from assumptions or environment-specific instructions.

## Working style

- Make the requested change directly; do not perform unrelated refactors.
- Before editing, inspect relevant files and current repository conventions.
- After editing, summarize files changed, tests actually run, and any checks that could not be run.
- If behavior or release semantics are ambiguous, inspect the existing implementation/configuration first and ask a concise clarifying question only when needed to avoid a risky assumption.
