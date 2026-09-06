# slack.grayoffice

Slack extension for Gray Office.

A Slack app that brings the Gray Office finance assistant into Slack. Mention it
in a channel or DM it; it forwards to the backend at
`POST <GRAYOFFICE_URL>/api/bots/ingest`, which does the AI routing — free text is
answered by the assistant, PDFs come back as structured JSON — and replies in
thread.

## Commands

Plain text, no slash-command setup required. Send the bot:

- `ping` — check responsiveness.
- `help` — what the bot can do.
- `login` — connect your Gray Office account. The bot posts a code and a link;
  once you enter the code at Gray Office, the message updates itself to
  **✅ Connected** automatically. Linked messages are tied to your account.
- `whoami` — show which Gray Office account is connected.
- `logout` — disconnect your Gray Office account.
- anything else — treated as a question for the assistant.

Share a **PDF** invoice/receipt and it comes back as structured JSON. Share a
**`.json`** document and it's validated locally, then summarised by the
assistant. Unsupported, oversized, or duplicate files get a clear error prompt.

> Slack keeps uploaded files private. PDFs are forwarded by URL; if the backend
> can't fetch a private file the bot says so. JSON files are downloaded by the
> app itself (with the bot token) so they always work.

## Setup

1. Create an app at https://api.slack.com/apps ("From scratch").
2. Under **Socket Mode**, enable it and create an app-level token with
   `connections:write` — this is `SLACK_APP_TOKEN` (`xapp-`).
3. Under **OAuth & Permissions**, add bot scopes `app_mentions:read`,
   `channels:history`, `im:history`, `chat:write`, `files:read`. Install to the
   workspace. The Bot User OAuth Token is `SLACK_BOT_TOKEN` (`xoxb-`).
4. Under **Event Subscriptions**, subscribe to bot events `app_mention` and
   `message.im`.
5. Invite the bot to a channel with `/invite @yourbot`.
6. Set `GRAYOFFICE_URL` and `BOT_INGEST_TOKEN` (must match grayoffice's
   `.dev.vars`) in the environment / `.env`.

## Run

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py
```
