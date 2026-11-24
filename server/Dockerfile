FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
RUN uv sync --frozen --no-dev

# Release builds put that version's client files here with SHA256SUMS (ADR-060); from source it
# holds only a README and the Downloads page says where to get the clients.
COPY downloads /downloads

ENV PATH="/app/.venv/bin:$PATH" \
    WATCHMAN_DATA_DIR=/data \
    WATCHMAN_MODELS_DIR=/models \
    WATCHMAN_DOWNLOADS_DIR=/downloads \
    WATCHMAN_BIND_HOST=127.0.0.1 \
    WATCHMAN_BIND_PORT=8030

ARG WATCHMAN_VERSION=""
ENV WATCHMAN_VERSION=$WATCHMAN_VERSION
LABEL org.opencontainers.image.source="https://github.com/jbarnell1/watchman" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

VOLUME ["/data"]
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"WATCHMAN_BIND_PORT\"]}/healthz').read()" || exit 1
CMD ["python", "-m", "app.main"]
