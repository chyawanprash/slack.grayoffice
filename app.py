import json
import os

import requests
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# grayoffice backend — the bot POSTs normalized events to /api/bots/ingest and the
# backend does the AI routing / PDF->JSON / audit logging, then returns the result.
GRAYOFFICE_URL = os.getenv("GRAYOFFICE_URL", "http://localhost:5173").rstrip("/")
BOT_INGEST_TOKEN = os.environ["BOT_INGEST_TOKEN"]
INGEST_ENDPOINT = f"{GRAYOFFICE_URL}/api/bots/ingest"

app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    # Socket Mode does not use request signing, so this check is not needed.
    request_verification_enabled=False,
)

BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]


def ingest(text: str, external_user: str, files: list[dict]) -> str:
    payload = {
        "source": "slack",
        "externalUser": external_user,
        "text": text,
        "files": files,
    }
    headers = {"Authorization": f"Bearer {BOT_INGEST_TOKEN}"}
    try:
        resp = requests.post(INGEST_ENDPOINT, json=payload, headers=headers, timeout=60)
        data = resp.json()
    except requests.RequestException as e:
        return f"Could not reach the backend: {e}"
    if resp.status_code != 200:
        return f"Backend error ({resp.status_code}): {data.get('error', data)}"
    return format_reply(data)


def format_reply(data: dict) -> str:
    detail = data.get("detail")
    if data.get("route") == "ask" and isinstance(detail, dict) and detail.get("reply"):
        return detail["reply"]
    if data.get("status") == "error":
        return f"Sorry, that failed: {detail}"
    if isinstance(detail, (dict, list)):
        return f"`{data.get('route')}` result:\n```{json.dumps(detail, indent=2)[:2800]}```"
    return str(detail or f"Processed ({data.get('route')}).")


def slack_files(event: dict) -> list[dict]:
    """Slack file URLs need the bot token to download; pass it through as a header
    hint is not supported, so we forward the private URL and let the backend try.
    For PDFs shared publicly this works; private files need url_private_download
    with an Authorization header (out of scope for this minimal bot)."""
    out = []
    for f in event.get("files", []) or []:
        out.append(
            {
                "name": f.get("name", "file"),
                "url": f.get("url_private_download") or f.get("url_private"),
                "mime": f.get("mimetype", ""),
            }
        )
    return out


@app.event("app_mention")
def handle_mention(event, say):
    text = event.get("text", "")
    # strip the leading <@BOTID> mention
    text = " ".join(p for p in text.split() if not p.startswith("<@")).strip()
    reply = ingest(text, event.get("user", "unknown"), slack_files(event))
    say(text=reply, thread_ts=event.get("thread_ts") or event["ts"])


@app.event("message")
def handle_message(event, say):
    # Only handle direct messages here; channel messages come via app_mention.
    if event.get("channel_type") != "im" or event.get("bot_id"):
        return
    reply = ingest(event.get("text", ""), event.get("user", "unknown"), slack_files(event))
    say(text=reply, thread_ts=event.get("thread_ts") or event["ts"])


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
