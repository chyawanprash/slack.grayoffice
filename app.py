import os

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    # Socket Mode does not use request signing, so this check is not needed.
    request_verification_enabled=False,
)


@app.message("ping")
def handle_ping(message, say):
    say(text="pong", thread_ts=message.get("thread_ts") or message["ts"])


@app.event("app_mention")
def handle_mention(event, say):
    text = event.get("text", "").lower()
    if "ping" in text:
        say(text="pong", thread_ts=event.get("thread_ts") or event["ts"])


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
