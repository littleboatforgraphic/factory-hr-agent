import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import emp
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from hr_agent import service
from hr_agent.bank import write_bank_file
from hr_agent.cloud_agent.agent import Agent, Tenant
from hr_agent.factory_node.app import create_app
from hr_agent.payslip import render_payslip_html
from hr_agent.storage.excel import MASTER_SHEETS

TOKENS = {
    "t-hr": {"user": "hr1", "role": "hr"},
    "t-acct": {"user": "acct", "role": "accountant"},
    "t-gm": {"user": "gm", "role": "management"},
    "t-agent": {"user": "agent", "role": "agent"},
    "t-f1": {"user": "f1", "role": "employee", "employee_id": "F1"},
}


@pytest.fixture
def seeded(factory, store, tmp_path):
    wb = load_workbook(store.master)
    for e in (emp("F1", device_user_id="101", language="ms"), emp("L1", "3000", "local", device_user_id="102")):
        wb["Employees"].append([e.model_dump().get(c) for c in MASTER_SHEETS["Employees"]])
    wb.save(store.master)
    csv = tmp_path / "att.csv"
    rows = ["AC-No.,Name,Time,State"]
    for day in range(1, 31):
        if day in (6, 13, 20, 27, 16):  # Sundays + Malaysia Day
            continue
        end = "13:00" if day in (5, 12, 19, 26) else "18:35"
        for dev in ("101", "102"):
            rows += [f"{dev},X,{day:02d}/09/2026 07:58:00,C/In", f"{dev},X,{day:02d}/09/2026 {end}:00,C/Out"]
    rows.append("999,Ghost,01/09/2026 08:00:00,C/In")
    csv.write_text("\n".join(rows) + "\n")
    return csv


def test_import_run_approve_lock_bank(factory, store, seeded, tmp_path):
    res = service.import_attendance(factory, store, seeded, "zkteco_csv", "hr1")
    assert res["unmapped_device_ids"] == {"999": 1} and res["punches_added"] == 100
    run = service.run_payroll(factory, store, "2026-09", "hr1")
    f1 = next(p for p in run.payslips if p.employee_id == "F1")
    assert f1.ot_hours_total == 21 * 1.5  # 21 weekdays x 17:00-18:35 -> 1.5 h
    assert "Gaji bersih" in render_payslip_html(f1, emp("F1", language="ms"), factory)
    with pytest.raises(PermissionError):
        service.transition(store, "2026-09", 1, "reviewed", "hr1", "hr")
    service.transition(store, "2026-09", 1, "reviewed", "acct", "accountant")
    service.transition(store, "2026-09", 1, "approved", "gm", "management")
    run = service.transition(store, "2026-09", 1, "locked", "acct", "accountant")
    emps = {e.employee_id: e for e in store.employees()}
    out = write_bank_file(run, emps, factory, tmp_path)
    assert out.read_text().count("SALARY 2026-09") == 2


def _fake_llm(calls):
    def create(**kw):
        calls.append(kw)
        if len(calls) == 1:
            tc = SimpleNamespace(id="c1", function=SimpleNamespace(name="get_payslip", arguments=json.dumps({"period": "2026-09", "employee_id": "L1"})))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=None))])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_node_api_access_and_agent_routing(factory, store, seeded):
    client = TestClient(create_app(factory, store, TOKENS))
    h = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
    with seeded.open("rb") as fh:
        r = client.post("/attendance/import", data={"profile": "zkteco_csv"}, files={"file": ("att.csv", fh)}, headers=h("t-hr"))
    assert r.status_code == 200, r.text
    assert client.post("/runs/2026-09", headers=h("t-acct")).status_code == 403
    assert client.post("/runs/2026-09", headers=h("t-hr")).status_code == 200
    assert client.get("/runs/2026-09", headers=h("bad")).status_code == 401
    assert client.get("/runs/2026-09/payslips/L1", headers=h("t-f1")).status_code == 403
    assert client.get("/runs/2026-09/payslips/F1", headers=h("t-f1")).status_code == 404  # not released until locked
    assert client.post("/runs/2026-09/1/approved", headers=h("t-gm")).status_code == 409  # must be reviewed first

    calls = []
    agent = Agent({factory.id: Tenant(factory.id, "http://node", "X")}, llm=_fake_llm(calls), model="m")
    client.headers.update(h("t-agent"))
    assert agent.ask(factory.id, "Why is L1's pay this amount?", http=client) == "done"
    tool_msg = calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and '"employee_id": "L1"' in tool_msg["content"]

    # An employee chatting is pinned to their own record even if the LLM asks for someone else.
    calls.clear()
    emp_client = TestClient(create_app(factory, store, TOKENS), headers=h("t-f1"))
    agent.ask(factory.id, "show L1", employee_id="F1", http=emp_client)
    assert [t["function"]["name"] for t in calls[0]["tools"]] == ["get_payslip"]
    assert "L1" not in calls[1]["messages"][-1]["content"]
    with pytest.raises(KeyError):
        agent.ask("other-factory", "hi", http=client)


def test_agent_meters_llm_calls_per_tenant(monkeypatch):
    from hr_agent.cloud_agent.agent import load_tenants, usage_user_id

    monkeypatch.setenv("LLM_API_KEY", "sk-default")
    monkeypatch.setenv("LLM_KEY_DEMO_PAHANG", "sk-pahang")
    tenants = load_tenants(Path(__file__).parent.parent / "config" / "tenants.example.yaml")
    assert tenants["demo-pahang"].llm_api_key() == "sk-pahang"
    assert Tenant("x", "http://n", "T").llm_api_key() == "sk-default"

    monkeypatch.setenv("LLM_BASE_URL", "http://gateway/v1")
    agent = Agent(tenants)
    llm = agent.llm_for(tenants["demo-pahang"])
    assert llm.api_key == "sk-pahang" and str(llm.base_url).startswith("http://gateway/v1")
    assert agent.llm_for(tenants["demo-pahang"]) is llm

    uid = usage_user_id("demo-pahang", "F1")
    assert uid.startswith("emp-") and "F1" not in uid and uid != usage_user_id("other", "F1")
    assert usage_user_id("demo-pahang", None) is None


def test_agent_sends_usage_tags(factory, store, seeded):
    calls = []
    agent = Agent({factory.id: Tenant(factory.id, "http://node", "X")}, llm=_fake_llm(calls), model="m")
    emp_client = TestClient(create_app(factory, store, TOKENS), headers={"Authorization": "Bearer t-f1"})
    agent.ask(factory.id, "my pay?", employee_id="F1", http=emp_client)
    assert calls[0]["extra_body"]["metadata"]["tags"] == [f"factory:{factory.id}", "op:employee-qa"]
    assert calls[0]["user"].startswith("emp-")


def test_error_flags_block_approval(factory, store, seeded):
    service.import_attendance(factory, store, seeded, "zkteco_csv", "hr1")
    run = service.run_payroll(factory, store, "2026-09", "hr1")
    run.flags.append(run.flags[0].model_copy(update={"severity": "error", "code": "missing_punch"}))
    store.save_run(run)  # v2 with an error flag
    service.transition(store, "2026-09", 2, "reviewed", "acct", "accountant")
    with pytest.raises(ValueError, match="blocking"):
        service.transition(store, "2026-09", 2, "approved", "gm", "management")


def test_ytd_accumulates_from_locked_runs(factory, store, seeded):
    service.import_attendance(factory, store, seeded, "zkteco_csv", "hr1")
    aug = service.run_payroll(factory, store, "2026-08", "hr1")
    for target, role in (("reviewed", "accountant"), ("approved", "management"), ("locked", "accountant")):
        service.transition(store, "2026-08", aug.version, target, "u", role)
    sep = service.run_payroll(factory, store, "2026-09", "hr1")
    a = next(p for p in aug.payslips if p.employee_id == "L1")
    s = next(p for p in sep.payslips if p.employee_id == "L1")
    assert s.ytd["NET"] == a.net + s.net
    assert s.ytd["EPF_EE"] == a.ytd["EPF_EE"] + next(ln.amount for ln in s.lines if ln.code == "EPF_EE")
    html = render_payslip_html(s, emp("L1", "3000", "local"), factory)
    assert "To Be Credited" in html and "YTD(MYR)" in html
