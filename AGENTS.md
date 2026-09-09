# Python Environment

Use the `.venv` virtual environment for all Python development in this project. Install dependencies only into `.venv`, never into the system Python.

# Language

Create all generated artifacts in English unless explicitly asked otherwise.

# Secrets

Never expose private data from environment files in the context window, whether by directly reading files or through commands and scripts.

# Design

Apply YAGNI and KISS: implement only what is needed, using the simplest clear solution.

# GitHub CLI

Run `gh` commands outside the sandbox.

# Bot Runtime

Use Docker Compose for deployment and configured end-to-end runs. Before starting, verify required configuration without printing secret values.

Published images are the default. To test local code, build an exact local image tag and set `BOT_IMAGE` to it and `BOT_PULL_POLICY=never`.

Production and staging must use distinct `COMPOSE_PROJECT_NAME`, Telegram bot token, webhook alias, webhook path, and webhook secret. Do not set `container_name`, bypass Compose with `docker run`, or replace configured runtime data with synthetic values.
