# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CONTROL_PANEL_HOST=0.0.0.0 \
    CONTROL_PANEL_PORT=8080 \
    CONTROL_PANEL_DATA_DIR=/data/control-panel \
    CONTROL_PANEL_ALLOW_LEGACY_IMPORT=false

RUN apt-get -o Acquire::http::Pipeline-Depth=0 update \
    && apt-get -o Acquire::http::Pipeline-Depth=0 install --yes --no-install-recommends ca-certificates ffmpeg libopus0 tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 bot \
    && useradd --uid 10001 --gid bot --create-home bot \
    && mkdir -p /data/control-panel /app \
    && chown -R 10001:10001 /data /app

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --requirement requirements.txt

# Explicit sources plus the allow-list in .dockerignore keep credentials,
# conversation archives, local environments and personal files out of images.
COPY --chown=10001:10001 *.py /app/
COPY --chown=10001:10001 providers /app/providers
COPY --chown=10001:10001 web /app/web

USER 10001:10001
EXPOSE 8080
VOLUME ["/data/control-panel"]
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()"]
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "web"]
