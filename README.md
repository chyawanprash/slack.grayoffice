# slack.grayoffice

A Slack app that forwards mentions and direct messages (and shared PDFs) to the
grayoffice backend at `POST <GRAYOFFICE_URL>/api/bots/ingest`. The backend does
the AI routing — free text is answered by the Gray Office finance assistant, PDFs
come back as structured JSON — and the app replies with the result.

## Setup

1. Create an app at https://api.slack.com/apps (use the "From scratch" option).
2. Under "Socket Mode", enable Socket Mode and create an app-level token with the `connections:write` scope. This is your `SLACK_APP_TOKEN` (starts with `xapp-`).
3. Under "OAuth & Permissions", add the bot scopes `app_mentions:read`, `channels:history`, `chat:write`. Install the app to the workspace. The Bot User OAuth Token is your `SLACK_BOT_TOKEN` (starts with `xoxb-`).
4. Under "Event Subscriptions", enable events and subscribe to the bot events `message.channels`, `message.im`, and `app_mention`. Add the `im:history` scope for DMs.
5. Invite the bot to a channel with `/invite @yourbot`.
6. Set `GRAYOFFICE_URL` and `BOT_INGEST_TOKEN` (must match grayoffice's `.dev.vars`) in the environment / `.env`.

## Run

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_APP_TOKEN=xapp-...
python app.py
```

Type `ping` in a channel where the bot is present and it replies `pong`.
