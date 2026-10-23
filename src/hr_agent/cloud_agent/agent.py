"""Multi-factory cloud agent. Routes each request to the right factory node and lets the LLM
(Ilmu Claw, via its OpenAI-compatible API) explain results using read-only tools.
The LLM never calculates pay: it only quotes figures and traces returned by the factory node.

LLM calls go through the llm-gateway (LLM_BASE_URL) with one gateway key per factory/client
(`llm_key_env` in the tenant registry), so token usage is metered and billed per client."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import yaml

SYSTEM_PROMPT = """You are an HR payroll assistant for a Malaysian factory.
Rules:
- Never calculate, estimate or change any pay figure. You may rank or compare employees using figures returned by tools. Only quote amounts and calculation traces returned by tools.
- If a tool does not return the answer, say you don't know and refer the user to HR.
- Reply in the user's language ({language}). Keep answers short and plain.
- Do not reveal one employee's data to another employee.
- Record replacement leave only when the HR officer instructs you to and says the employee and the approving
  manager have confirmed. Repeat back the employee, days, date worked and manager in your reply."""

TOOLS = [
    {"type": "function", "function": {"name": "get_run_summary", "description": "Totals and flag counts for a payroll period",
        "parameters": {"type": "object", "properties": {"period": {"type": "string", "description": "YYYY-MM"}}, "required": ["period"]}}},
    {"type": "function", "function": {"name": "get_flags", "description": "Anomaly and compliance flags for a payroll period",
        "parameters": {"type": "object", "properties": {"period": {"type": "string"}}, "required": ["period"]}}},
    {"type": "function", "function": {"name": "get_attendance",
        "description": "Attendance per employee for a payroll period: present/absent/late days, late minutes, missing "
                       "punches, normal and OT hours, rest-day/public-holiday work, leave taken. Filter by department "
                       "or employee; the response lists all departments.",
        "parameters": {"type": "object", "properties": {"period": {"type": "string", "description": "YYYY-MM"},
                       "department": {"type": "string"}, "employee_id": {"type": "string"}}, "required": ["period"]}}},
    {"type": "function", "function": {"name": "get_payslip", "description": "Payslip lines with calculation traces for one employee",
        "parameters": {"type": "object", "properties": {"period": {"type": "string"}, "employee_id": {"type": "string"}}, "required": ["period", "employee_id"]}}},
]

# Only offered when an HR officer is chatting; the factory node checks the officer again.
HR_WRITE_TOOLS = [
    {"type": "function", "function": {"name": "record_replacement_leave",
        "description": "Record replacement leave days granted to an employee, on the HR officer's instruction",
        "parameters": {"type": "object", "properties": {
            "employee_id": {"type": "string"},
            "days": {"type": "number", "description": "0.5 or whole days"},
            "worked_on": {"type": "string", "description": "YYYY-MM-DD the employee worked (rest day / public holiday)"},
            "reason": {"type": "string"},
            "employee_confirmed": {"type": "boolean", "description": "HR officer confirmed with the employee"},
            "manager_confirmed_by": {"type": "string", "description": "Name of the manager who approved"},
        }, "required": ["employee_id", "days", "worked_on", "employee_confirmed", "manager_confirmed_by"]}}},
]


@dataclass
class Tenant:
    factory_id: str
    node_url: str
    token_env: str
    llm_key_env: str | None = None  # env var holding this client's llm-gateway key; falls back to LLM_API_KEY
    # Chat channels (one Telegram bot and one WhatsApp number per factory); secrets come from env vars.
    telegram_token_env: str | None = None
    telegram_secret_env: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_token_env: str | None = None

    def llm_api_key(self) -> str | None:
        return (os.environ.get(self.llm_key_env) if self.llm_key_env else None) or os.environ.get("LLM_API_KEY")

    def client(self, transport: httpx.BaseTransport | None = None) -> httpx.Client:
        token = os.environ.get(self.token_env, "")
        return httpx.Client(base_url=self.node_url, headers={"Authorization": f"Bearer {token}"}, timeout=30, transport=transport)


def load_tenants(path: str | Path) -> dict[str, Tenant]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {t["factory_id"]: Tenant(**t) for t in data.get("tenants", [])}


class FactoryTools:
    """Read-only tools bound to one tenant and, for employees, one employee id."""

    def __init__(self, http: httpx.Client, employee_id: str | None = None, hr_user: str | None = None):
        self.http = http
        self.employee_id = employee_id
        self.hr_user = hr_user

    def _get(self, path: str) -> Any:
        r = self.http.get(path)
        if r.status_code >= 400:
            return {"error": r.status_code, "detail": r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text}
        return r.json()

    def _post(self, path: str, body: dict, params: dict) -> Any:
        r = self.http.post(path, json=body, params=params)
        if r.status_code >= 400:
            return {"error": r.status_code, "detail": r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text}
        return r.json()

    def call(self, name: str, args: dict) -> Any:
        period = args.get("period", "")
        if name == "record_replacement_leave":
            if self.employee_id is not None or not self.hr_user:
                return {"error": 403, "detail": "Only an HR officer can instruct the agent to record leave"}
            worked_on = str(args.get("worked_on") or "")
            body = {**{k: args.get(k) for k in ("employee_id", "days", "reason", "employee_confirmed", "manager_confirmed_by")},
                    "worked_on": worked_on or None, "year": int(worked_on[:4]) if worked_on[:4].isdigit() else None}
            return self._post("/leave/replacement", {k: v for k, v in body.items() if v is not None},
                              {"on_behalf_of": self.hr_user})
        if self.employee_id is not None:
            if name != "get_payslip":
                return {"error": 403, "detail": "Employees may only view their own payslip"}
            args["employee_id"] = self.employee_id
        if name == "get_run_summary":
            return self._get(f"/runs/{period}")
        if name == "get_flags":
            return self._get(f"/runs/{period}/flags")
        if name == "get_attendance":
            params = {k: args[k] for k in ("department", "employee_id") if args.get(k)}
            return self._get(f"/runs/{period}/attendance" + ("?" + urlencode(params) if params else ""))
        if name == "get_payslip":
            released = "?released_only=true" if self.employee_id is not None else ""
            return self._get(f"/runs/{period}/payslips/{args['employee_id']}{released}")
        return {"error": 400, "detail": f"Unknown tool {name}"}


def usage_user_id(factory_id: str, employee_id: str | None) -> str | None:
    """Pseudonymous end-user id for gateway usage logs (never the raw employee id or name)."""
    if employee_id is None:
        return None
    return "emp-" + hashlib.sha256(f"{factory_id}:{employee_id}".encode()).hexdigest()[:12]


class Agent:
    def __init__(self, tenants: dict[str, Tenant], llm=None, model: str | None = None):
        self.tenants = tenants
        self.model = model or os.environ.get("LLM_MODEL", "ilmu-claw")
        self._llm = llm  # injected client (tests) is shared by all tenants
        self._llm_by_tenant: dict[str, Any] = {}

    def llm_for(self, tenant: Tenant):
        if self._llm is not None:
            return self._llm
        if tenant.factory_id not in self._llm_by_tenant:
            from openai import OpenAI

            self._llm_by_tenant[tenant.factory_id] = OpenAI(base_url=os.environ.get("LLM_BASE_URL"), api_key=tenant.llm_api_key())
        return self._llm_by_tenant[tenant.factory_id]

    def ask(self, factory_id: str, message: str, *, language: str = "en", employee_id: str | None = None,
            hr_user: str | None = None, http: httpx.Client | None = None, max_steps: int = 5) -> str:
        """employee_id: an employee chatting about their own payslip. hr_user: an HR officer (enables HR tools)."""
        tenant = self.tenants.get(factory_id)
        if tenant is None:
            raise KeyError(f"Unknown factory {factory_id!r}")
        tools = FactoryTools(http or tenant.client(), employee_id, hr_user if employee_id is None else None)
        llm = self.llm_for(tenant)
        metering: dict[str, Any] = {"extra_body": {"metadata": {"tags": [
            f"factory:{factory_id}", "op:employee-qa" if employee_id is not None else "op:hr-qa"]}}}
        if (user := usage_user_id(factory_id, employee_id)) is not None:
            metering["user"] = user
        tool_list = [t for t in TOOLS if employee_id is None or t["function"]["name"] == "get_payslip"]
        if employee_id is None and hr_user:
            tool_list += HR_WRITE_TOOLS
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(language=language)},
            {"role": "user", "content": message},
        ]
        for _ in range(max_steps):
            resp = llm.chat.completions.create(model=self.model, messages=messages, tools=tool_list, **metering)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return msg.content or ""
            messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [{"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                result = tools.call(tc.function.name, json.loads(tc.function.arguments or "{}"))
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)})
        return "Sorry, I could not complete that request. Please contact HR."
