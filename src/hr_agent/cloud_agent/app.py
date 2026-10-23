"""Cloud agent web app: Telegram and WhatsApp webhooks for all factories.

Env:
  HR_TENANTS             tenant registry YAML (factory id -> node URL, token env, channel settings)
  HR_CHAT_STAFF          staff YAML (HR / accountant / management chat accounts per factory)
  WHATSAPP_APP_SECRET    Meta app secret, verifies X-Hub-Signature-256
  WHATSAPP_VERIFY_TOKEN  token entered in Meta's webhook settings
  per tenant: telegram_token_env, telegram_secret_env, whatsapp_token_env (see tenants.example.yaml)

Run: uvicorn hr_agent.cloud_agent.app:app_factory --factory
Register a Telegram webhook: python -m hr_agent.cloud_agent.app set-telegram-webhook <factory_id> <public base URL>
"""

from __future__ import annotations

import hmac
import logging
import os
import sys
from collections import OrderedDict
from collections.abc import Callable
from functools import lru_cache

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse

from hr_agent.channels.gateway import TEXTS, ChatGateway, Sender, load_staff
from hr_agent.channels.telegram import TelegramClient, parse_update
from hr_agent.channels.whatsapp import WhatsAppClient, parse_messages, verify_signature
from hr_agent.cloud_agent.agent import Agent, Tenant, load_tenants

log = logging.getLogger(__name__)


class _Seen:
    """Webhooks can be retried; remember recent message ids so each message is answered once."""

    def __init__(self, size: int = 5000):
        self.size, self.ids = size, OrderedDict()

    def first_time(self, key) -> bool:
        if key in self.ids:
            return False
        self.ids[key] = None
        if len(self.ids) > self.size:
            self.ids.popitem(last=False)
        return True


def _env(name: str | None) -> str:
    return os.environ.get(name, "") if name else ""


def default_telegram(tenant: Tenant) -> TelegramClient:
    return TelegramClient(_env(tenant.telegram_token_env))


def default_whatsapp(tenant: Tenant) -> WhatsAppClient:
    return WhatsAppClient(_env(tenant.whatsapp_token_env), tenant.whatsapp_phone_number_id or "")


def create_cloud_app(
    gateway: ChatGateway,
    *,
    telegram: Callable[[Tenant], TelegramClient] = default_telegram,
    whatsapp: Callable[[Tenant], WhatsAppClient] = default_whatsapp,
    whatsapp_app_secret: str | None = None,
    whatsapp_verify_token: str | None = None,
) -> FastAPI:
    app = FastAPI(title="HR cloud agent")
    seen = _Seen()
    tenants = gateway.agent.tenants

    @app.get("/health")
    def health():
        return {"ok": True, "factories": len(tenants)}

    def process_telegram(tenant: Tenant, update: dict) -> None:
        parsed = parse_update(update)
        if parsed is None:
            return
        sender, chat_id, text, contact = parsed
        if contact is not None:
            if str(contact.get("user_id")) != sender.chat_id:
                reply = gateway.handle(tenant.factory_id, sender, "/start")
                reply.text, reply.ask_contact = TEXTS["own_contact"], True
            else:
                reply = gateway.link_telegram(tenant.factory_id, sender, contact.get("phone_number", ""))
        elif text:
            reply = gateway.handle(tenant.factory_id, sender, text)
        else:
            reply = gateway.handle(tenant.factory_id, sender, "/start")
            if not reply.ask_contact:
                reply.text = TEXTS["text_only"]
        try:
            with telegram(tenant) as client:
                client.send(chat_id, reply.text, reply.ask_contact)
        except Exception as e:  # no traceback: request URLs/headers can carry the bot token
            log.error("Telegram send failed for factory %s (%s)", tenant.factory_id, type(e).__name__)

    @app.post("/telegram/{factory_id}")
    async def telegram_webhook(factory_id: str, request: Request, background: BackgroundTasks,
                               secret: str = Header("", alias="X-Telegram-Bot-Api-Secret-Token")):
        tenant = tenants.get(factory_id)
        if tenant is None:
            raise HTTPException(404, "Unknown factory")
        expected = _env(tenant.telegram_secret_env)
        if not expected or not hmac.compare_digest(secret, expected):
            raise HTTPException(403, "Bad secret token")
        update = await request.json()
        if seen.first_time(("telegram", factory_id, update.get("update_id"))):
            background.add_task(process_telegram, tenant, update)
        return {"ok": True}

    @app.get("/whatsapp", response_class=PlainTextResponse)
    def whatsapp_verify(request: Request):
        q = request.query_params
        if whatsapp_verify_token and q.get("hub.mode") == "subscribe" and \
                hmac.compare_digest(q.get("hub.verify_token", ""), whatsapp_verify_token):
            return q.get("hub.challenge", "")
        raise HTTPException(403, "Verification failed")

    def process_whatsapp(tenant: Tenant, msg: dict) -> None:
        sender = Sender("whatsapp", msg["from"])
        if msg["type"] == "text" and msg["text"]:
            reply = gateway.handle(tenant.factory_id, sender, msg["text"])
        else:
            reply = gateway.handle(tenant.factory_id, sender, "/start")
            if reply.text == TEXTS["welcome"]:
                reply.text = TEXTS["text_only"]
        try:
            with whatsapp(tenant) as client:
                client.send(msg["from"], reply.text)
        except Exception as e:  # no traceback: request URLs/headers can carry the bot token
            log.error("WhatsApp send failed for factory %s (%s)", tenant.factory_id, type(e).__name__)

    @app.post("/whatsapp")
    async def whatsapp_webhook(request: Request, background: BackgroundTasks,
                               signature: str = Header("", alias="X-Hub-Signature-256")):
        raw = await request.body()
        if not verify_signature(whatsapp_app_secret, raw, signature):
            raise HTTPException(403, "Bad signature")
        by_number = {t.whatsapp_phone_number_id: t for t in tenants.values() if t.whatsapp_phone_number_id}
        for msg in parse_messages(await request.json()):
            tenant = by_number.get(msg["phone_number_id"])
            if tenant is None or not msg["from"] or not seen.first_time(("whatsapp", msg["id"])):
                continue
            background.add_task(process_whatsapp, tenant, msg)
        return {"ok": True}  # always 200 quickly so Meta does not retry

    return app


@lru_cache
def _default_app() -> FastAPI:
    tenants = load_tenants(os.environ["HR_TENANTS"])
    staff = load_staff(os.environ["HR_CHAT_STAFF"]) if os.environ.get("HR_CHAT_STAFF") else []
    return create_cloud_app(ChatGateway(Agent(tenants), staff),
                            whatsapp_app_secret=os.environ.get("WHATSAPP_APP_SECRET"),
                            whatsapp_verify_token=os.environ.get("WHATSAPP_VERIFY_TOKEN"))


def app_factory() -> FastAPI:
    return _default_app()


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] != "set-telegram-webhook":
        print(__doc__)
        return 2
    factory_id, base_url = argv[1], argv[2].rstrip("/")
    tenant = load_tenants(os.environ["HR_TENANTS"])[factory_id]
    with default_telegram(tenant) as client:
        print(client.set_webhook(f"{base_url}/telegram/{factory_id}", _env(tenant.telegram_secret_env)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
