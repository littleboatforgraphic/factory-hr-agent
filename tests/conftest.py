from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from hr_agent.config import load_factory
from hr_agent.models import Employee, Punch
from hr_agent.storage.excel import ExcelStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def factory(tmp_path):
    f = load_factory(ROOT / "config/factories/demo-pahang.yaml")
    return f.model_copy(update={"storage": f.storage.model_copy(update={"path": str(tmp_path / "store")})})


@pytest.fixture
def store(factory):
    s = ExcelStore(factory.resolve(factory.storage.path))
    s.init_master()
    return s


def emp(eid="F1", salary="1700", category="foreign", **kw) -> Employee:
    return Employee(employee_id=eid, name=eid, category=category, join_date=date(2020, 1, 1),
                    monthly_salary=Decimal(salary), bank_account=f"acct-{eid}", **kw)


def punches(eid: str, *pairs: tuple[str, str]) -> list[Punch]:
    out = []
    for a, b in pairs:
        out.append(Punch(employee_id=eid, timestamp=datetime.fromisoformat(a), direction="in"))
        out.append(Punch(employee_id=eid, timestamp=datetime.fromisoformat(b), direction="out"))
    return out
