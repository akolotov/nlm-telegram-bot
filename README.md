# Gemini Notebook Telegram Bot

Python Telegram bot deployed by webhook through a shared Tailscale Funnel
gateway. Send it a YouTube URL and it temporarily adds the video to the
`NLM-bot-placeholder` notebook, asks NotebookLM for a structured summary, and
returns the Markdown result as a Telegram Rich Message reply. The temporary
source and query conversation are removed afterward.

## Development

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install ".[dev]"
.venv/bin/python -m pytest -q
```

Copy `.env.example` to `.env` and replace every placeholder. `ALLOWED_USER_IDS`
is a comma-separated allowlist of Telegram numeric user IDs; updates from all
other users are silently ignored. Each staging or production deployment needs
a separate Telegram bot token, Compose project name, webhook alias, webhook
path, and webhook secret.

The alias must match the corresponding segment in `WEBHOOK_PATH`. The shared
gateway must provide the external `tailscale-ingress` Docker network.

```bash
docker compose pull
docker compose up -d
docker compose logs -f bot
```

For a local build:

```bash
docker build -t nlm-telegram-bot:local .
BOT_IMAGE=nlm-telegram-bot:local BOT_PULL_POLICY=never docker compose up -d
```

## Gemini Notebook authentication

The container uses manually exported Google cookies. It does not install or
run Chrome. While signed in to `https://notebook.google.com/`, export the
Google cookies to `cookies.txt` in this directory. The importer accepts JSON,
Netscape / Mozilla cookie format, or a raw `Cookie` header.

Both `cookies.txt` and `.nlm-runtime/` contain credentials. They are excluded
from Git and the Docker build context. Never commit them, bake them into an
image, or expose their contents in logs.

### How to Extract Cookies Manually

The following instructions are copied from the
[gemini-notebook-mcp-cli GitHub repository's `AUTHENTICATION.md`](https://github.com/jacob-bd/gemini-notebook-mcp-cli/blob/main/docs/AUTHENTICATION.md#how-to-extract-cookies-manually).

1. Open Chrome and go to `https://notebook.google.com`.
2. Make sure you are logged in.
3. Press F12 (or Cmd+Option+I on macOS) to open DevTools.
4. Select the **Network** tab.
5. Enter `batchexecute` in the filter box.
6. Open any notebook to trigger a request.
7. Select a `batchexecute` request from the list.
8. In the right panel, scroll to **Request Headers**.
9. Find the header beginning with `cookie:`.
10. Copy the header's cookie value.
11. Paste it into a text file and save it as `cookies.txt`.

The commands below use the local image. Build it and select the exact tag for
Compose:

```bash
docker build -t nlm-telegram-bot:local .
export BOT_IMAGE=nlm-telegram-bot:local
export BOT_PULL_POLICY=never
```

Create the persistent runtime directory, then import the cookies into its
`default` profile:

```bash
mkdir -p .nlm-runtime

docker compose --profile tools run --rm --no-deps \
  -v "${PWD}/cookies.txt:/run/secrets/notebooklm-cookies.txt:ro" \
  nlm-tools \
  login --manual --profile default \
  --file /run/secrets/notebooklm-cookies.txt
```

Compose mounts the host directory `./.nlm-runtime` at
`/state/notebooklm` inside the container. Verify the imported credentials:

```bash
docker compose --profile tools run --rm --no-deps \
  nlm-tools \
  login --check --profile default
```

The client refreshes CSRF and session tokens over HTTP while the imported
Google cookies remain valid. When Google eventually rejects the cookies,
export a fresh `cookies.txt` and repeat the import. Manual import does not
create a browser profile, so this image intentionally has no headless-Chrome
fallback. If cleanup of a generated conversation is rejected by NotebookLM,
the bot logs the cleanup problem but still delivers a successfully generated
summary and removes the temporary source.

## Local NotebookLM profile path

The same `.nlm-runtime` can be used outside Docker. Set its host path in the
shell or in `.env`:

```dotenv
NOTEBOOKLM_MCP_CLI_PATH="~/projects/nlm-telegram-bot/.nlm-runtime"
```

The bot expands `~` in `NOTEBOOKLM_MCP_CLI_PATH` before loading
`notebooklm-tools`.
