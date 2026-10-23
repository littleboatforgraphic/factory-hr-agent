"""Telegram Bot API: parse webhook updates and send replies."""

from __future__ import annotations

import httpx

from hr_agent.channels.gateway import Sender

API = "https://api.telegram.org"
MAX_TEXT = 4096
CONTACT_BUTTON = "Share my phone number / Kongsi nombor telefon"


class TelegramClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(base_url=f"{API}/bot{token}", timeout=20, transport=transport)

    def close(self) -> None:
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def send(self, chat_id: str | int, text: str, ask_contact: bool = False) -> None:
        markup = ({"keyboard": [[{"text": CONTACT_BUTTON, "request_contact": True}]], "one_time_keyboard": True,
                   "resize_keyboard": True} if ask_contact else {"remove_keyboard": True})
        r = self.http.post("/sendMessage", json={"chat_id": chat_id, "text": text[:MAX_TEXT], "reply_markup": markup})
        r.raise_for_status()

    def set_webhook(self, url: str, secret_token: str) -> dict:
        r = self.http.post("/setWebhook", json={"url": url, "secret_token": secret_token, "allowed_updates": ["message"]})
        r.raise_for_status()
        return r.json()


def parse_update(update: dict) -> tuple[Sender, int, str | None, dict | None] | None:
    """(sender, chat id to reply to, text, contact) for a private-chat message; None for anything else."""
    msg = update.get("message") or {}
    chat, user = msg.get("chat") or {}, msg.get("from") or {}
    if chat.get("type") != "private" or not user.get("id") or user.get("is_bot"):
        return None
    sender = Sender("telegram", str(user["id"]), (user.get("language_code") or "")[:2])
    return sender, chat["id"], msg.get("text"), msg.get("contact")
