FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    WATCHMAN_DATA_DIR=/data \
    WATCHMAN_MODELS_DIR=/models \
    WATCHMAN_BIND_HOST=127.0.0.1 \
    WATCHMAN_BIND_PORT=8030

VOLUME ["/data"]
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"WATCHMAN_BIND_PORT\"]}/healthz').read()" || exit 1
CMD ["python", "-m", "app.main"]
