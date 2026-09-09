# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
RUN python -m venv "$VIRTUAL_ENV" \
    && pip install --upgrade pip setuptools wheel
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

FROM python:3.12-slim AS runtime

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NOTEBOOKLM_MCP_CLI_PATH=/state/notebooklm

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home --home-dir /home/app app \
    && mkdir -p /app /state \
    && chown -R app:app /app /state

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
USER app
ENTRYPOINT ["nlm-telegram-bot"]
