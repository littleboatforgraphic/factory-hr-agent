from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from hr_agent.config import FactoryConfig
from hr_agent.models import Employee, Payslip
from hr_agent.money import ZERO, D
from hr_agent.payslip.i18n import label
from hr_agent.periods import month_bounds, pay_date

_env = Environment(loader=FileSystemLoader(Path(__file__).parent), autoescape=select_autoescape())


def _fmt(amount) -> str:
    return f"{D(amount):,.2f}"


def _neg(amount) -> str:
    """Deductions in brackets, e.g. '(     407.00)'."""
    return "(" + f"{_fmt(amount)})".rjust(13)


def build_rows(payslip: Payslip, L) -> list[dict]:  # noqa: N803
    ytd = payslip.ytd

    def item(ln, bracket=False):
        text = L(ln.code, ln.label)
        if ln.quantity and ln.code.startswith(("OT_", "UNPAID")):
            text += f" ({ln.quantity}{' h' if ln.code.startswith('OT_') else ' d'})"
        cur, total = ln.amount, ytd.get(ln.code, ln.amount)
        return {"kind": "item", "text": text, "cur": _neg(cur) if bracket else _fmt(cur), "ytd": _neg(total) if bracket else _fmt(total)}

    rows: list[dict] = [{"kind": "section", "text": L("earnings")}]
    rows += [item(ln) for ln in payslip.lines if ln.kind == "earning"]
    rows.append({"kind": "tot", "text": L("gross"), "cur": _fmt(payslip.gross), "ytd": _fmt(ytd.get("GROSS", payslip.gross))})
    rows.append({"kind": "section", "text": L("deductions")})
    rows += [item(ln, bracket=True) for ln in payslip.lines if ln.kind == "deduction"]
    rows.append({"kind": "tot", "text": L("total_deductions"), "cur": _neg(payslip.total_deductions),
                 "ytd": _neg(ytd.get("DEDUCTIONS", payslip.total_deductions))})
    rows.append({"kind": "gap"})
    rows.append({"kind": "net", "text": L("net"), "cur": _fmt(payslip.net), "ytd": _fmt(ytd.get("NET", payslip.net))})
    rows.append({"kind": "gap"})
    rows += [{**item(ln), "kind": "plain", "text": L(ln.code + "_FULL", ln.label)} for ln in payslip.lines if ln.kind == "employer"]
    return rows


def render_payslip_html(payslip: Payslip, employee: Employee, factory: FactoryConfig) -> str:
    lang = employee.language if employee.language in factory.payslip_languages else "en"

    def L(key: str, fallback: str | None = None) -> str:  # noqa: N802
        item = factory.pay_items.get(key.removesuffix("_FULL"))
        if item:
            other = item.labels.get(lang)
            return f"{item.name} / {other}" if other and lang != "en" else item.name
        return label(key, lang, fallback)

    days = [d for d in payslip.days if d.status != "not_employed"]
    att = {
        "working": sum(1 for d in days if d.day_type == "normal"),
        "present": sum(1 for d in days if d.status in ("present", "incomplete") and d.day_type == "normal"),
        "leave": sum((d.leave_days for d in days), ZERO),
        "absent": sum(1 for d in days if d.status == "absent"),
    }
    start, end = month_bounds(payslip.period)
    salary = D(employee.monthly_salary)
    daily = salary / factory.ot.orp_divisor_days
    hourly = daily / factory.ot.hourly_divisor_hours
    logo = getattr(factory.employer, "logo_file", None)
    return _env.get_template("template.html").render(
        L=L, e=employee, p=payslip, employer=factory.employer,
        logo=factory.resolve(logo).as_uri() if logo else None,
        location=" / ".join(x for x in (employee.department, factory.state) if x),
        period_range=f"{start:%d %b %Y} - {end:%d %b %Y}",
        basic_salary=_fmt(salary), daily=_fmt(daily), hourly=f"{hourly:,.3f}",
        net=_fmt(payslip.net), pay_date=pay_date(payslip.period, factory.calendar).strftime("%d %b %Y"),
        att_range=f"{payslip.attendance_start:%d/%m}-{payslip.attendance_end:%d/%m/%Y}",
        rows=build_rows(payslip, L), att=att,
    )


def render_payslip_pdf(payslip: Payslip, employee: Employee, factory: FactoryConfig, out: str | Path) -> Path:
    from weasyprint import HTML

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=render_payslip_html(payslip, employee, factory)).write_pdf(out)
    return out
