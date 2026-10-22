"""WhatsApp Cloud API (Meta): verify and parse webhooks, send replies."""

from __future__ import annotations

import hashlib
import hmac
import os

import httpx

GRAPH = "https://graph.facebook.com"
MAX_TEXT = 4096


def verify_signature(app_secret: str | None, raw_body: bytes, header: str | None) -> bool:
    """Meta signs each webhook body with the app secret: X-Hub-Signature-256: sha256=<hex>."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.removeprefix("sha256="), expected)


def parse_messages(payload: dict) -> list[dict]:
    """Incoming messages as {phone_number_id, id, from, type, text}; delivery status events are skipped."""
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}
            pnid = (value.get("metadata") or {}).get("phone_number_id")
            for m in value.get("messages", []):
                out.append({"phone_number_id": pnid, "id": m.get("id"), "from": m.get("from"), "type": m.get("type"),
                            "text": (m.get("text") or {}).get("body")})
    return out


class WhatsAppClient:
    def __init__(self, token: str, phone_number_id: str, version: str | None = None,
                 transport: httpx.BaseTransport | None = None):
        self.version = version or os.environ.get("WHATSAPP_GRAPH_VERSION", "v21.0")
        self.phone_number_id = phone_number_id
        self.http = httpx.Client(base_url=GRAPH, headers={"Authorization": f"Bearer {token}"}, timeout=20,
                                 transport=transport)

    def send(self, to: str, text: str) -> None:
        r = self.http.post(f"/{self.version}/{self.phone_number_id}/messages", json={
            "messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text[:MAX_TEXT]}})
        r.raise_for_status()
