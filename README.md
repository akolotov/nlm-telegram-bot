# YouTube Summary Telegram Bot

This Python Telegram bot summarizes public YouTube videos with the Google Gen AI SDK.
Send a YouTube URL and choose a stream overview or a talk breakdown with actionable items.
The bot returns the selected Markdown summary as a Telegram Rich Message reply.
Both prompts request headings and lists without Markdown tables.

The bot uses webhooks through a shared Tailscale Funnel gateway.
`ALLOWED_USER_IDS` is a comma-separated list of Telegram user IDs that can use the bot.
Other users cannot request summaries.

## Development and configuration

Create the virtual environment and install the project dependencies:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install ".[dev]"
.venv/bin/python -m pytest -q
```

Copy `.env.example` to `.env` and replace the placeholders.
Set `GEMINI_API_KEY` to your Gemini Developer API key.
The bot refuses to start without this key.
Keep `.env` out of Git, build images, and logs.

`GEMINI_MODEL` selects the model.
If this variable is absent or blank, the bot uses `gemini-3.8-flash`.
An alternative model must support video input from YouTube URLs.
The bot sends one generation request per summary and does not retry failed requests.

Requests have a 600-second timeout and an output limit of 8,192 tokens.
The bot rejects empty, blocked, and incomplete summaries.
If Gemini returns HTTP 429, the bot reports that the service reached a rate limit.
It does not queue requests or enforce local request quotas.

Logs include the operation ID, model, duration, and available token counts.
They do not include the API key or summary text.
Token counts do not report the remaining daily request quota.
View project usage in [Google AI Studio](https://aistudio.google.com/usage).

## Deployment

Production and staging must use separate Telegram bot tokens, Compose project names, webhook aliases, webhook paths, and webhook secrets.
The webhook alias must match the corresponding segment in `WEBHOOK_PATH`.
The shared gateway must provide the external `tailscale-ingress` Docker network.
The bot does not require a NotebookLM profile or cookies.

To deploy a published image with the configuration in `.env`:

```bash
docker compose pull
docker compose up -d
```

For a local build, use the exact `BOT_IMAGE` value from `.env`.
The following command reads that value without printing environment file contents:

```bash
.venv/bin/python - <<'PY'
import subprocess
from dotenv import dotenv_values

image = dotenv_values(".env").get("BOT_IMAGE", "").strip()
if not image:
    raise SystemExit("BOT_IMAGE is required for a local build")
subprocess.run(["docker", "build", "-t", image, "."], check=True)
PY
BOT_PULL_POLICY=never docker compose up -d --pull never
```

The local build command rebuilds the configured image.
The Compose command then replaces the running bot with that image.
Use `docker compose logs -f bot` to read application logs.
