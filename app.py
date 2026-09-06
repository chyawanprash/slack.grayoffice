"""Gray Office Slack app.

Forwards mentions, DMs and file uploads to the grayoffice backend
(`POST <GRAYOFFICE_URL>/api/bots/ingest`), which does the AI routing, PDF->JSON
conversion and audit logging. Focuses on the Slack experience: account linking
that confirms itself, upload validation, and a friendly error prompt on failure.

Commands are plain text (no slash-command config needed): send `login`, `logout`,
`whoami`, `help`, or `ping` to the bot; anything else is treated as a question.
"""

import json
import os
import threading
import time

import requests
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

GRAYOFFICE_URL = os.getenv("GRAYOFFICE_URL", "http://localhost:5173").rstrip("/")
BOT_INGEST_TOKEN = os.environ["BOT_INGEST_TOKEN"]
BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]

INGEST_ENDPOINT = f"{GRAYOFFICE_URL}/api/bots/ingest"
LINK_START_ENDPOINT = f"{GRAYOFFICE_URL}/api/bots/link/start"
LINK_STATUS_ENDPOINT = f"{GRAYOFFICE_URL}/api/bots/link/status"
LINK_REVOKE_ENDPOINT = f"{GRAYOFFICE_URL}/api/bots/link/revoke"
HEADERS = {"Authorization": f"Bearer {BOT_INGEST_TOKEN}"}

SOURCE = "slack"
PDF_EXTS = {".pdf"}
JSON_EXTS = {".json"}
ALLOWED_EXTS = PDF_EXTS | JSON_EXTS
MAX_BYTES = 25 * 1024 * 1024
JSON_CONTEXT_LIMIT = 6000
SLACK_LIMIT = 3800

HELP_TEXT = (
    "*Gray Office — finance ops in Slack*\n"
    "• Ask a finance / books / GST question and I'll answer.\n"
    "• Share a PDF invoice or receipt — I return structured JSON.\n"
    "• Share a `.json` document — I validate it and summarise it.\n\n"
    "`login` — connect your Gray Office account\n"
    "`whoami` — show the connected account\n"
    "`logout` — disconnect\n"
    "`ping` — check I'm alive"
)

app = App(token=BOT_TOKEN, request_verification_enabled=False)


class BackendError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def ext_of(name: str) -> str:
    return os.path.splitext(name or "")[1].lower()


# ------------------------------------------------------------------ backend I/O


def _json_or_raise(resp: requests.Response) -> dict:
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        raise BackendError(
            f"Unexpected response ({resp.status_code}): {resp.text[:300]}", resp.status_code
        )
    if resp.status_code != 200:
        msg = data.get("error") if isinstance(data, dict) else data
        raise BackendError(str(msg or f"HTTP {resp.status_code}"), resp.status_code)
    return data


def _post(url: str, payload: dict) -> dict:
    try:
        return _json_or_raise(requests.post(url, json=payload, headers=HEADERS, timeout=90))
    except requests.RequestException as e:
        raise BackendError(f"network error: {e}") from e


def _get(url: str, params: dict) -> dict:
    try:
        return _json_or_raise(requests.get(url, params=params, headers=HEADERS, timeout=30))
    except requests.RequestException as e:
        raise BackendError(f"network error: {e}") from e


def call_ingest(text: str, external_user: str, files: list[dict]) -> dict:
    return _post(
        INGEST_ENDPOINT,
        {"source": SOURCE, "externalUser": external_user, "text": text, "files": files},
    )


def slack_download(url: str) -> bytes:
    r = requests.get(url, headers={"Authorization": f"Bearer {BOT_TOKEN}"}, timeout=30)
    r.raise_for_status()
    return r.content


# ---------------------------------------------------------------- presentation


def backend_err_text(exc: BackendError) -> str:
    if exc.status is None:
        return (
            "⚠️ *Can't reach Gray Office*\n"
            "The backend didn't respond — it may be starting up or offline. "
            "Give it a moment and try again."
        )
    if exc.status in (401, 403):
        return (
            "⚠️ *Not authorized*\n"
            "The backend rejected the bot's ingest token. "
            "Check `BOT_INGEST_TOKEN` matches the backend's value."
        )
    return f"⚠️ *Backend error ({exc.status})*\n{exc}"


def render_reply(data: dict, source_name: str | None = None) -> str:
    route = data.get("route")
    detail = data.get("detail")

    if data.get("status") == "error":
        msg = detail.get("error") if isinstance(detail, dict) else detail
        return f"⚠️ *Processing failed*\n{msg or 'unknown error'}"

    if route == "ask" and isinstance(detail, dict) and detail.get("reply"):
        prefix = f"🧾 *{source_name}*\n" if source_name else ""
        return prefix + detail["reply"]

    if route == "pdf-to-json" and isinstance(detail, dict):
        results = detail.get("results") or []
        if not results:
            return "⚠️ No data extracted from the document."
        chunks = []
        for r in results:
            fname = r.get("file", "document")
            pretty = json.dumps(r.get("json"), indent=2, ensure_ascii=False)
            if len(pretty) > 3000:
                pretty = pretty[:3000] + "\n…(truncated)"
            chunks.append(f"📄 *{fname}* → structured JSON ✅\n```\n{pretty}\n```")
        return "\n\n".join(chunks)

    return f"✅ *Processed ({route})*\n```\n{json.dumps(detail, indent=2)[:1500]}\n```"


def deliver(say, thread_ts: str, text: str) -> None:
    text = text or "Done."
    for i in range(0, len(text), SLACK_LIMIT):
        say(text=text[i : i + SLACK_LIMIT], thread_ts=thread_ts)


# ------------------------------------------------------------------- documents


def sort_files(file_objs: list[dict]) -> tuple[list[dict], list[tuple[str, str]]]:
    accepted: list[dict] = []
    rejected: list[tuple[str, str]] = []
    seen: set[tuple[str, int]] = set()
    for f in file_objs:
        name = f.get("name", "file")
        ext = ext_of(name)
        size = f.get("size") or 0
        key = (name.lower(), size)
        if ext not in ALLOWED_EXTS:
            rejected.append((name, "unsupported type — only `.pdf` and `.json`"))
        elif size > MAX_BYTES:
            rejected.append((name, f"too large ({size / 1_048_576:.1f} MB · max 25 MB)"))
        elif key in seen:
            rejected.append((name, "duplicate of another attachment"))
        else:
            seen.add(key)
            accepted.append(f)
    accepted.sort(key=lambda f: ext_of(f.get("name", "")) not in PDF_EXTS)
    return accepted, rejected


def process_files(file_objs: list[dict], caption: str | None, external_user: str) -> str:
    accepted, rejected = sort_files(file_objs)
    out: list[str] = []

    if rejected:
        out.append(
            f"⚠️ *Skipped {len(rejected)} attachment(s)*\n"
            + "\n".join(f"• *{n}* — {why}" for n, why in rejected)
        )
    if not accepted:
        return "\n\n".join(out) or "⚠️ Share a `.pdf` or `.json` file."

    pdfs = [f for f in accepted if ext_of(f.get("name", "")) in PDF_EXTS]
    jsons = [f for f in accepted if ext_of(f.get("name", "")) in JSON_EXTS]

    if pdfs:
        payload = [
            {
                "name": f.get("name", "file"),
                "url": f.get("url_private_download") or f.get("url_private"),
                "mime": f.get("mimetype", "application/pdf"),
            }
            for f in pdfs
        ]
        try:
            out.append(render_reply(call_ingest(caption or "", external_user, payload)))
        except BackendError as e:
            out.append(
                backend_err_text(e)
                + "\n_Slack keeps uploaded files private; if the backend couldn't fetch it, that's why._"
            )

    for f in jsons:
        name = f.get("name", "file")
        try:
            raw = slack_download(f.get("url_private_download") or f.get("url_private"))
            pretty = json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
        except (requests.RequestException, json.JSONDecodeError, UnicodeDecodeError) as e:
            out.append(f"⚠️ *Couldn't read {name}*\nnot valid JSON — {e}")
            continue
        prompt = f"{caption}\n\n" if caption else ""
        prompt += f"Attached JSON document `{name}`:\n```json\n{pretty[:JSON_CONTEXT_LIMIT]}\n```"
        if len(pretty) > JSON_CONTEXT_LIMIT:
            prompt += "\n(truncated)"
        if not caption:
            prompt += "\n\nSummarise this document and flag anything that needs attention."
        try:
            out.append(render_reply(call_ingest(prompt, external_user, []), source_name=name))
        except BackendError as e:
            out.append(backend_err_text(e))

    return "\n\n".join(out)


# -------------------------------------------------------------- account linking


def do_login(say, event: dict, user: str) -> None:
    thread_ts = event.get("thread_ts") or event["ts"]
    try:
        data = _post(
            LINK_START_ENDPOINT,
            {"source": SOURCE, "externalUser": user, "displayName": user},
        )
    except BackendError as e:
        say(text=backend_err_text(e), thread_ts=thread_ts)
        return

    code, url = data["code"], data["url"]
    mins = round(data.get("expiresInSeconds", 900) / 60)
    res = say(
        text=(
            "🔗 *Connect your Gray Office account*\n"
            f"1. Open <{url}|Gray Office> and sign in.\n"
            "2. Enter this code:\n\n"
            f"`{code}`\n\n"
            f"Expires in ~{mins} min. I'll update this message once you're connected."
        ),
        thread_ts=thread_ts,
    )
    threading.Thread(
        target=_await_link, args=(res["channel"], res["ts"], user), daemon=True
    ).start()


def _await_link(channel: str, ts: str, user: str) -> None:
    for _ in range(40):  # ~3.5 min
        time.sleep(5)
        try:
            status = _get(LINK_STATUS_ENDPOINT, {"source": SOURCE, "externalUser": user})
        except BackendError:
            continue
        if status.get("linked"):
            who = status.get("name") or status.get("email") or "your account"
            try:
                app.client.chat_update(
                    channel=channel,
                    ts=ts,
                    text=(
                        f"✅ *Connected*\nYou're linked to Gray Office as *{who}*. "
                        "Everything you send me now is tied to your account."
                    ),
                )
            except Exception:
                pass
            return


def do_whoami(say, thread_ts: str, user: str) -> None:
    try:
        status = _get(LINK_STATUS_ENDPOINT, {"source": SOURCE, "externalUser": user})
    except BackendError as e:
        say(text=backend_err_text(e), thread_ts=thread_ts)
        return
    if not status.get("linked"):
        say(text="Not connected. Send `login` to link a Gray Office account.", thread_ts=thread_ts)
        return
    who = status.get("name") or status.get("email")
    say(text=f"✅ Connected to Gray Office as *{who}*.", thread_ts=thread_ts)


def do_logout(say, thread_ts: str, user: str) -> None:
    try:
        data = _post(LINK_REVOKE_ENDPOINT, {"source": SOURCE, "externalUser": user})
    except BackendError as e:
        say(text=backend_err_text(e), thread_ts=thread_ts)
        return
    say(
        text=(
            "Disconnected from Gray Office."
            if data.get("revoked")
            else "You weren't connected to a Gray Office account."
        ),
        thread_ts=thread_ts,
    )


# --------------------------------------------------------------------- routing


def route_text(text: str) -> tuple[str, str]:
    low = text.strip().lower()
    if low in ("ping",):
        return "ping", ""
    if low in ("help", "?", "commands"):
        return "help", ""
    if low in ("login", "connect", "link"):
        return "login", ""
    if low in ("logout", "disconnect", "unlink"):
        return "logout", ""
    if low in ("whoami", "status", "account"):
        return "whoami", ""
    if low.startswith("ask "):
        return "ask", text.strip()[4:].strip()
    return "ask", text.strip()


def dispatch(event: dict, say, text: str) -> None:
    user = event.get("user", "unknown")
    thread_ts = event.get("thread_ts") or event["ts"]

    files = event.get("files") or []
    if files:
        deliver(say, thread_ts, process_files(files, text or None, user))
        return

    cmd, arg = route_text(text)
    if cmd == "ping":
        say(text="pong 🏓", thread_ts=thread_ts)
    elif cmd == "help":
        say(text=HELP_TEXT, thread_ts=thread_ts)
    elif cmd == "login":
        do_login(say, event, user)
    elif cmd == "logout":
        do_logout(say, thread_ts, user)
    elif cmd == "whoami":
        do_whoami(say, thread_ts, user)
    elif not arg:
        say(text="Ask me a finance question, or share a PDF / JSON.", thread_ts=thread_ts)
    else:
        try:
            reply = render_reply(call_ingest(arg, user, []))
        except BackendError as e:
            reply = backend_err_text(e)
        deliver(say, thread_ts, reply)


@app.event("app_mention")
def handle_mention(event, say):
    text = " ".join(p for p in event.get("text", "").split() if not p.startswith("<@")).strip()
    dispatch(event, say, text)


@app.event("message")
def handle_message(event, say):
    if event.get("bot_id") or event.get("channel_type") != "im":
        return
    subtype = event.get("subtype")
    if subtype and subtype != "file_share":
        return
    dispatch(event, say, (event.get("text") or "").strip())


if __name__ == "__main__":
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
