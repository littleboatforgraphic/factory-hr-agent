"""Bank payment files. Formats are registered per bank; the factory config picks one."""

from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path

from hr_agent.config import FactoryConfig
from hr_agent.models import Employee, PayrollRun
from hr_agent.periods import pay_date

BankWriter = Callable[[PayrollRun, dict[str, Employee], FactoryConfig, Path], Path]
FORMATS: dict[str, BankWriter] = {}


def register(name: str):
    def deco(fn: BankWriter) -> BankWriter:
        FORMATS[name] = fn
        return fn

    return deco


@register("hlb_bulk")
def hlb_bulk(run: PayrollRun, employees: dict[str, Employee], factory: FactoryConfig, out: Path) -> Path:
    """PLACEHOLDER layout. Replace with Hong Leong Connect's official bulk-payment template once supplied."""
    paid = pay_date(run.period, factory.calendar)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["payment_date", "beneficiary_name", "beneficiary_bank", "account_no", "id_no", "amount", "reference"])
        for p in run.payslips:
            e = employees[p.employee_id]
            w.writerow([f"{paid:%d%m%Y}", e.name, e.bank_name, e.bank_account, e.id_no, f"{p.net:.2f}", f"SALARY {run.period}"])
    return out


def write_bank_file(run: PayrollRun, employees: dict[str, Employee], factory: FactoryConfig, out_dir: Path) -> Path:
    if run.status not in ("approved", "locked"):
        raise ValueError("Bank files can only be generated for approved or locked payroll")
    fmt = factory.bank.file_format
    if fmt not in FORMATS:
        raise ValueError(f"Unknown bank file format {fmt!r}; available: {sorted(FORMATS)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    return FORMATS[fmt](run, employees, factory, out_dir / f"bank_{factory.bank.bank_code}_{run.period}_v{run.version}.csv")
