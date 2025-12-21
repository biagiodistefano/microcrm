# syntax=docker/dockerfile:1.4
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

ENV UV_SYSTEM_PYTHON=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV PYTHONUNBUFFERED=1
ENV SECRET_KEY=build-secret
ENV DEBUG=0

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-editable

COPY . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable

ENV STATIC_ROOT=/app/staticfiles
RUN mkdir -p ${STATIC_ROOT}
RUN uv run python src/manage.py collectstatic --noinput

# ─────────────────────────────────────────────

FROM python:3.14-slim-bookworm AS runtime

RUN useradd --create-home --system --shell /bin/bash appuser

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /app/src /app/src
COPY --from=builder --chown=appuser:appuser /app/staticfiles /app/staticfiles
COPY --from=builder --chown=appuser:appuser /app/entrypoint.sh /app/entrypoint.sh

ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

WORKDIR /app/src
USER appuser

RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
