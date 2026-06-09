# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# uv (pinned) for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

WORKDIR /app

# Resolve + install deps first (cached layer; no project yet).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# App sources. The rubric skill ships IN the image — the loader reads it at runtime.
COPY tutor ./tutor
COPY grounding_mcp ./grounding_mcp
COPY .claude ./.claude
COPY api ./api
RUN uv sync --frozen --no-dev

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "tutor.app:app", "--host", "0.0.0.0", "--port", "8000"]
