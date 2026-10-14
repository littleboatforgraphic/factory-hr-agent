"""Multi-factory cloud agent. Routes each request to the right factory node and lets the LLM
(Ilmu Claw, via its OpenAI-compatible API) explain results using read-only tools.
The LLM never calculates pay: it only quotes figures and traces returned by the factory node."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

SYSTEM_PROMPT = """You are an HR payroll assistant for a Malaysian factory.
Rules:
- Never calculate, estimate or change any pay figure. Only quote amounts and calculation traces returned by tools.
- If a tool does not return the answer, say you don't know and refer the user to HR.
- Reply in the user's language ({language}). Keep answers short and plain.
- Do not reveal one employee's data to another employee."""

TOOLS = [
    {"type": "function", "function": {"name": "get_run_summary", "description": "Totals and flag counts for a payroll period",
        "parameters": {"type": "object", "properties": {"period": {"type": "string", "description": "YYYY-MM"}}, "required": ["period"]}}},
    {"type": "function", "function": {"name": "get_flags", "description": "Anomaly and compliance flags for a payroll period",
        "parameters": {"type": "object", "properties": {"period": {"type": "string"}}, "required": ["period"]}}},
    {"type": "function", "function": {"name": "get_payslip", "description": "Payslip lines with calculation traces for one employee",
        "parameters": {"type": "object", "properties": {"period": {"type": "string"}, "employee_id": {"type": "string"}}, "required": ["period", "employee_id"]}}},
]


@dataclass
class Tenant:
    factory_id: str
    node_url: str
    token_env: str

    def client(self, transport: httpx.BaseTransport | None = None) -> httpx.Client:
        token = os.environ.get(self.token_env, "")
        return httpx.Client(base_url=self.node_url, headers={"Authorization": f"Bearer {token}"}, timeout=30, transport=transport)


def load_tenants(path: str | Path) -> dict[str, Tenant]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {t["factory_id"]: Tenant(**t) for t in data.get("tenants", [])}


class FactoryTools:
    """Read-only tools bound to one tenant and, for employees, one employee id."""

    def __init__(self, http: httpx.Client, employee_id: str | None = None):
        self.http = http
        self.employee_id = employee_id

    def _get(self, path: str) -> Any:
        r = self.http.get(path)
        if r.status_code >= 400:
            return {"error": r.status_code, "detail": r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text}
        return r.json()

    def call(self, name: str, args: dict) -> Any:
        period = args.get("period", "")
        if self.employee_id is not None:
            if name != "get_payslip":
                return {"error": 403, "detail": "Employees may only view their own payslip"}
            args["employee_id"] = self.employee_id
        if name == "get_run_summary":
            return self._get(f"/runs/{period}")
        if name == "get_flags":
            return self._get(f"/runs/{period}/flags")
        if name == "get_payslip":
            return self._get(f"/runs/{period}/payslips/{args['employee_id']}")
        return {"error": 400, "detail": f"Unknown tool {name}"}


class Agent:
    def __init__(self, tenants: dict[str, Tenant], llm=None, model: str | None = None):
        self.tenants = tenants
        self.model = model or os.environ.get("LLM_MODEL", "ilmu-claw")
        if llm is None:
            from openai import OpenAI

            llm = OpenAI(base_url=os.environ.get("LLM_BASE_URL"), api_key=os.environ.get("LLM_API_KEY"))
        self.llm = llm

    def ask(self, factory_id: str, message: str, *, language: str = "en", employee_id: str | None = None,
            http: httpx.Client | None = None, max_steps: int = 5) -> str:
        tenant = self.tenants.get(factory_id)
        if tenant is None:
            raise KeyError(f"Unknown factory {factory_id!r}")
        tools = FactoryTools(http or tenant.client(), employee_id)
        tool_list = [t for t in TOOLS if employee_id is None or t["function"]["name"] == "get_payslip"]
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(language=language)},
            {"role": "user", "content": message},
        ]
        for _ in range(max_steps):
            resp = self.llm.chat.completions.create(model=self.model, messages=messages, tools=tool_list)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return msg.content or ""
            messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [{"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                result = tools.call(tc.function.name, json.loads(tc.function.arguments or "{}"))
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)})
        return "Sorry, I could not complete that request. Please contact HR."
