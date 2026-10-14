from __future__ import annotations

import calendar
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from hr_agent.config import FactoryConfig
from hr_agent.models import Employee, Payslip
from hr_agent.money import ZERO
from hr_agent.payslip.i18n import label
from hr_agent.periods import parse_period, pay_date

_env = Environment(loader=FileSystemLoader(Path(__file__).parent), autoescape=select_autoescape())


def _fmt(amount) -> str:
    return f"{amount:,.2f}"


def render_payslip_html(payslip: Payslip, employee: Employee, factory: FactoryConfig) -> str:
    lang = employee.language if employee.language in factory.payslip_languages else "en"

    def L(key: str, fallback: str | None = None) -> str:  # noqa: N802
        return label(key, lang, fallback)

    def rows(kind: str):
        out = []
        for ln in payslip.lines:
            if ln.kind != kind:
                continue
            text = L(ln.code, ln.label)
            if ln.quantity and ln.code.startswith(("OT_", "UNPAID")):
                text += f" ({ln.quantity}{' h' if ln.code.startswith('OT_') else ' d'})"
            out.append({"text": text, "amount": _fmt(ln.amount)})
        return out

    days = [d for d in payslip.days if d.status != "not_employed"]
    att = {
        "working": sum(1 for d in days if d.day_type == "normal"),
        "present": sum(1 for d in days if d.status in ("present", "incomplete") and d.day_type == "normal"),
        "leave": sum((d.leave_days for d in days), ZERO),
        "absent": sum(1 for d in days if d.status == "absent"),
    }
    y, m = parse_period(payslip.period)
    p = payslip.model_copy(update={"gross": _fmt(payslip.gross), "net": _fmt(payslip.net), "total_deductions": _fmt(payslip.total_deductions)})
    return _env.get_template("template.html").render(
        L=L, e=employee, p=p, employer=factory.employer,
        period_label=f"{calendar.month_name[m]} {y}",
        pay_date=pay_date(payslip.period, factory.calendar).strftime("%d/%m/%Y"),
        att_range=f"{payslip.attendance_start:%d/%m}–{payslip.attendance_end:%d/%m/%Y}",
        earnings=rows("earning"), deductions=rows("deduction"), employer_lines=rows("employer"), att=att,
    )


def render_payslip_pdf(payslip: Payslip, employee: Employee, factory: FactoryConfig, out: str | Path) -> Path:
    from weasyprint import HTML

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=render_payslip_html(payslip, employee, factory)).write_pdf(out)
    return out
