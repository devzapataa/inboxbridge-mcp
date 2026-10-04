# syntax=docker/dockerfile:1
FROM python:3.14-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Primero solo las dependencias, para que Docker las cachee entre cambios de código.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14-slim
RUN useradd --system --uid 10001 --no-create-home inboxbridge
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" HOST=0.0.0.0 PORT=8000 PYTHONUNBUFFERED=1
USER inboxbridge
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/salud', timeout=4)"]
CMD ["inboxbridge"]
