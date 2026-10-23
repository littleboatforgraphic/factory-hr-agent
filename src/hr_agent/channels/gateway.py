"""Routes chat messages to the cloud agent with the sender's identity and permissions.

Staff (HR, accountant, management) are listed in the cloud-side staff file. Employees are recognised by the
factory node from the Telegram id / phone number in their own master file; the cloud keeps no employee data."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
import yaml

from hr_agent.cloud_agent.agent import Agent, Tenant
from hr_agent.phone import normalize_phone

log = logging.getLogger(__name__)
STAFF_ROLES = {"hr", "accountant", "management"}

TEXTS = {
    "unregistered_telegram": "Hi! I can't find you yet. Please tap \"Share my phone number\" so I can match you with "
    "your employee record.\nHai! Sila tekan \"Kongsi nombor telefon\" supaya saya boleh padankan anda dengan rekod pekerja.",
    "unregistered_whatsapp": "Sorry, this WhatsApp number is not registered with HR. Please ask HR to update your phone "
    "number.\nMaaf, nombor WhatsApp ini tidak berdaftar dengan HR. Sila minta HR mengemas kini nombor telefon anda.",
    "linked": "Thanks, you're now linked to your employee record. Ask me about your payslip, OT or leave.\n"
    "Terima kasih, anda telah dipadankan dengan rekod pekerja. Tanya saya tentang slip gaji, OT atau cuti anda.",
    "not_matched": "Sorry, that phone number doesn't match our records. Please contact HR.\n"
    "Maaf, nombor telefon itu tiada dalam rekod kami. Sila hubungi HR.",
    "own_contact": "Please share your own phone number using the button below.\n"
    "Sila kongsi nombor telefon anda sendiri dengan butang di bawah.",
    "unavailable": "Sorry, the HR system is not reachable right now. Please try again later or contact HR.\n"
    "Maaf, sistem HR tidak dapat dihubungi sekarang. Sila cuba lagi kemudian atau hubungi HR.",
    "welcome": "Hi! Ask me about your payslip, OT or leave.\nHai! Tanya saya tentang slip gaji, OT atau cuti anda.",
    "text_only": "Sorry, I can only read text messages.\nMaaf, saya hanya boleh membaca mesej teks.",
}


@dataclass
class StaffMember:
    factory_id: str
    user: str  # must match the user name in the factory node's tokens (checked for HR actions)
    role: str
    telegram_id: str = ""
    whatsapp: str = ""
    language: str = "en"
    # Required for WhatsApp: the staff member's employee id; their number must still match master.xlsx.
    employee_id: str = ""


def load_staff(path: str | Path) -> list[StaffMember]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [StaffMember(**{k: str(v) for k, v in s.items()}) for s in data.get("staff", [])]


@dataclass
class Sender:
    channel: Literal["telegram", "whatsapp"]
    chat_id: str  # Telegram user id, or WhatsApp phone number (wa_id)
    language: str = ""


@dataclass
class Reply:
    text: str
    ask_contact: bool = False


class ChatGateway:
    def __init__(self, agent: Agent, staff: list[StaffMember],
                 node_client: Callable[[Tenant], httpx.Client] | None = None):
        self.agent = agent
        self.staff = staff
        self.node_client = node_client or (lambda t: t.client())

    def tenant(self, factory_id: str) -> Tenant:
        return self.agent.tenants[factory_id]

    def staff_member(self, factory_id: str, sender: Sender) -> StaffMember | None:
        for s in self.staff:
            if s.factory_id != factory_id or s.role not in STAFF_ROLES:
                continue
            if sender.channel == "telegram" and s.telegram_id and s.telegram_id == sender.chat_id:
                return s
            if sender.channel == "whatsapp" and s.whatsapp and normalize_phone(s.whatsapp) == normalize_phone(sender.chat_id):
                return s
        return None

    @staticmethod
    def _employee(http: httpx.Client, sender: Sender) -> dict | None:
        params = {"telegram_id": sender.chat_id} if sender.channel == "telegram" else {"phone": sender.chat_id}
        r = http.get("/directory/employee", params=params)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def handle(self, factory_id: str, sender: Sender, text: str) -> Reply:
        tenant = self.tenant(factory_id)
        try:
            with self.node_client(tenant) as http:
                staff = self.staff_member(factory_id, sender)
                if staff is not None and sender.channel == "whatsapp":
                    # Phone numbers get reassigned: only honour a staff number that still belongs to that
                    # current employee in the factory's master file.
                    current = self._employee(http, sender)
                    if not staff.employee_id or not current or current.get("employee_id") != staff.employee_id:
                        log.warning("Staff WhatsApp number for %s/%s no longer matches master", factory_id, staff.user)
                        staff = None
                if staff is not None:
                    if text.strip() == "/start":
                        return Reply(TEXTS["welcome"])
                    hr_user = staff.user if staff.role == "hr" else None
                    return Reply(self.agent.ask(factory_id, text, language=staff.language or sender.language or "en",
                                                hr_user=hr_user, staff_user=staff.user, http=http))
                employee = self._employee(http, sender)
                if employee is None:
                    return Reply(TEXTS[f"unregistered_{sender.channel}"], ask_contact=sender.channel == "telegram")
                if text.strip() == "/start":
                    return Reply(TEXTS["welcome"])
                return Reply(self.agent.ask(factory_id, text, language=employee.get("language") or "en",
                                            employee_id=employee["employee_id"], http=http))
        except Exception:  # never leak internals to the chat; message content is not logged
            log.exception("Chat request for factory %s failed", factory_id)
            return Reply(TEXTS["unavailable"])

    def link_telegram(self, factory_id: str, sender: Sender, phone: str) -> Reply:
        tenant = self.tenant(factory_id)
        try:
            with self.node_client(tenant) as http:
                r = http.post("/directory/link", json={"channel": "telegram", "chat_id": sender.chat_id, "phone": phone})
        except httpx.HTTPError:
            log.exception("Linking Telegram account for factory %s failed", factory_id)
            return Reply(TEXTS["unavailable"])
        if r.status_code == 404:
            return Reply(TEXTS["not_matched"])
        if r.status_code >= 400:
            return Reply(TEXTS["unavailable"])
        return Reply(TEXTS["linked"])
