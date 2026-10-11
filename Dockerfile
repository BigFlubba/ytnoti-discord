FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CONFIG_PATH=/config/config.yaml

# Unprivileged user; /data holds state and must be writable by it.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data /config \
 && chown app:app /data

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY ytnoti_discord ./ytnoti_discord

USER app
VOLUME ["/data"]
EXPOSE 8000

# Healthy only once the public callback URL works and subscriptions are requested.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

ENTRYPOINT ["python", "-m", "ytnoti_discord"]
