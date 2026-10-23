"""Deterministic payroll engine. The AI agent never computes pay; it reads these results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from hr_agent.config import FactoryConfig
from hr_agent.models import (
    DEFAULT_LEAVE_PAID,
    DayResult,
    Employee,
    Flag,
    Line,
    PayItemEntry,
    Payslip,
    TaxInfo,
)
from hr_agent.money import ZERO, D, round_cents
from hr_agent.periods import month_bounds, parse_period
from hr_agent.statutory.my import MalaysiaStatutory


@dataclass
class YearToDate:
    normal_taxable: Decimal = ZERO
    epf_employee: Decimal = ZERO
    pcb: Decimal = ZERO
    tp1_deductions: Decimal = ZERO
    zakat: Decimal = ZERO
    items: dict[str, Decimal] = field(default_factory=dict)  # per line code, earlier months this year


def _within_annual_cap(ytd: Decimal, amount: Decimal, cap: Decimal) -> Decimal:
    """Part of this month's amount that still fits under an annual cap."""
    return max(min(ytd + amount, cap) - min(ytd, cap), ZERO)


@dataclass
class _Builder:
    lines: list[Line] = field(default_factory=list)

    def add(self, kind, code, label, amount, trace="", quantity=None, rate=None) -> Decimal:
        amount = round_cents(amount)
        if amount != 0:
            self.lines.append(
                Line(kind=kind, code=code, label=label, amount=amount, trace=trace, quantity=quantity, rate=rate)
            )
        return amount


def _age_on(dob: date | None, on: date) -> int | None:
    if not dob:
        return None
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def rest_day_pay(hours: Decimal, orp: Decimal, hrp: Decimal, normal_hours: Decimal, policy_rate: Decimal):
    """Employment Act s.60(3) for monthly-rated employees vs. the factory's policy; pay the higher."""
    if hours <= normal_hours / 2:
        ea = orp * Decimal("0.5")
    elif hours <= normal_hours:
        ea = orp
    else:
        ea = orp + (hours - normal_hours) * hrp * 2
    policy = hours * hrp * policy_rate
    return (ea, "Employment Act s.60(3)") if ea > policy else (policy, f"policy {policy_rate}x hourly")


def public_holiday_pay(hours: Decimal, orp: Decimal, hrp: Decimal, normal_hours: Decimal, policy_rate: Decimal):
    """Employment Act s.60D(3): 2 days' wages, plus 3x hourly beyond normal hours; pay the higher."""
    ea = orp * 2 + max(hours - normal_hours, ZERO) * hrp * 3
    policy = hours * hrp * policy_rate
    return (ea, "Employment Act s.60D(3)") if ea > policy else (policy, f"policy {policy_rate}x hourly")


def compute_payslip(
    *,
    employee: Employee,
    factory: FactoryConfig,
    period: str,
    days: list[DayResult],
    attendance_start: date,
    attendance_end: date,
    pay_items: list[PayItemEntry],
    ytd: YearToDate,
    statutory: MalaysiaStatutory,
) -> tuple[Payslip, list[Flag]]:
    eid = employee.employee_id
    flags: list[Flag] = []

    def flag(severity, code, message):
        flags.append(Flag(severity=severity, code=code, message=message, employee_id=eid))

    b = _Builder()
    month_start, month_end = month_bounds(period)
    days_in_month = (month_end - month_start).days + 1
    salary = D(employee.monthly_salary)
    ot = factory.ot
    orp = salary / ot.orp_divisor_days
    hrp = orp / ot.hourly_divisor_hours

    # Basic pay, prorated by calendar days for joiners/leavers.
    emp_start = max(month_start, employee.join_date)
    emp_end = min(month_end, employee.exit_date) if employee.exit_date else month_end
    employed_days = max((emp_end - emp_start).days + 1, 0)
    if employed_days < days_in_month:
        basic = salary * employed_days / days_in_month
        basic_trace = f"RM{salary} x {employed_days}/{days_in_month} calendar days employed"
    else:
        basic = salary
        basic_trace = "Full month"
    basic = b.add("earning", "BASIC", "Basic salary", basic, basic_trace)

    if salary < factory.minimum_wage_monthly:
        flag("error", "below_minimum_wage", f"Salary RM{salary} is below minimum wage RM{factory.minimum_wage_monthly}")

    # Unpaid leave and absence.
    leave_paid = {**DEFAULT_LEAVE_PAID, **factory.leave_paid}
    unpaid_days = sum(
        (r.leave_days for r in days if r.leave_type and not leave_paid.get(r.leave_type.value, True)), ZERO
    ) + sum((Decimal(1) for r in days if r.status == "absent"), ZERO)
    if unpaid_days:
        if factory.unpaid_divisor == "calendar_days":
            divisor, label = Decimal(days_in_month), f"{days_in_month} calendar days"
        elif factory.unpaid_divisor == "fixed_26":
            divisor, label = Decimal(26), "26 days"
        else:
            working = sum(1 for r in days if r.day_type == "normal" and r.status != "not_employed")
            divisor, label = Decimal(working or 1), f"{working} working days"
        b.add(
            "earning",
            "UNPAID",
            "Unpaid leave / absence",
            -(salary / divisor * unpaid_days),
            f"RM{salary} / {label} x {unpaid_days} day(s)",
            quantity=unpaid_days,
        )

    # Overtime.
    ot_hours_total = ZERO
    normal_ot_hours = sum((r.ot_hours for r in days if r.day_type == "normal"), ZERO)
    if normal_ot_hours:
        b.add(
            "earning",
            "OT_NORMAL",
            f"Overtime {ot.normal_day_rate}x",
            normal_ot_hours * hrp * ot.normal_day_rate,
            f"{normal_ot_hours} h x hourly RM{round_cents(hrp)} (RM{salary}/{ot.orp_divisor_days}/"
            f"{ot.hourly_divisor_hours}) x {ot.normal_day_rate}",
            quantity=normal_ot_hours,
            rate=round_cents(hrp * ot.normal_day_rate),
        )
        ot_hours_total += normal_ot_hours
    for day_type, code, label, fn, rate in (
        ("rest", "OT_REST", "Rest day work", rest_day_pay, ot.rest_day_rate),
        ("public_holiday", "OT_PH", "Public holiday work", public_holiday_pay, ot.public_holiday_rate),
    ):
        worked = [r for r in days if r.day_type == day_type and r.worked_hours > 0]
        if not worked:
            continue
        total, traces, hours = ZERO, [], ZERO
        for r in worked:
            amount, basis = fn(r.worked_hours, orp, hrp, ot.hourly_divisor_hours, rate)
            total += amount
            hours += r.worked_hours
            traces.append(f"{r.date:%d/%m} {r.worked_hours} h -> RM{round_cents(amount)} ({basis})")
        b.add("earning", code, label, total, "; ".join(traces), quantity=hours)
        ot_hours_total += hours
    if ot_hours_total > ot.monthly_cap_hours:
        flag("block", "ot_over_cap", f"{ot_hours_total} OT hours exceeds the {ot.monthly_cap_hours} h monthly limit")
    elif ot_hours_total >= ot.warn_at_hours:
        flag("warning", "ot_near_cap", f"{ot_hours_total} OT hours is close to the {ot.monthly_cap_hours} h limit")

    # Company pay items: allowances, incentives, benefits in kind, employer contributions, deductions.
    epf_extra = epf_extra_ar = socso_extra = eis_extra = hrd_extra = ZERO
    taxable_normal = taxable_additional = tax_exempt = bik = tp1 = zakat = ZERO
    later_deductions: list[tuple[str, str, Decimal, str]] = []
    for entry in pay_items:
        if not entry.applies_to(period, month_start, month_end):
            continue
        code, item = entry.item, factory.pay_items.get(entry.item)
        if item is None:
            flag("error", "unknown_pay_item", f"Pay item {code!r} RM{entry.amount} is not in this company's pay-item list")
            continue
        if item.requires_approval and not entry.approved_by:
            flag("warning", "pay_item_not_approved", f"{item.name} RM{entry.amount} excluded: not approved")
            continue
        amount = round_cents(entry.amount)
        trace = f"Pay item {code}" + (f", approved by {entry.approved_by}" if entry.approved_by else "")
        trace += f" ({entry.note})" if entry.note else ""
        prior = D(ytd.items.get(code, ZERO))
        if item.type == "deduction":
            if item.tp1_relief_annual_cap is not None:
                relief = _within_annual_cap(prior, amount, item.tp1_relief_annual_cap)
                tp1 += relief
                trace += f"; TP1 relief RM{relief} (cap RM{item.tp1_relief_annual_cap}/year)"
            if item.zakat:
                zakat += amount
            later_deductions.append((code, item.name, amount, trace))
            continue

        if item.tax == "exempt":
            exempt = amount if item.tax_exempt_annual_limit is None else _within_annual_cap(
                prior, amount, item.tax_exempt_annual_limit)
            taxable = amount - exempt
            trace += f"; tax exempt RM{exempt}" + (f", taxable RM{taxable}" if taxable else "")
        else:
            exempt, taxable = ZERO, amount
        tax_exempt += exempt
        if item.tax == "additional":
            taxable_additional += taxable
        else:
            taxable_normal += taxable

        if item.type == "employer_contribution":
            b.add("employer", code, item.name, amount, trace)
        elif item.type == "benefit_in_kind":
            bik += b.add("benefit", code, item.name, amount, trace + "; non-cash, for PCB only")
        else:
            b.add("earning", code, item.name, amount, trace)
            if item.epf:
                if item.tax == "additional":
                    epf_extra_ar += amount
                else:
                    epf_extra += amount
            socso_extra += amount if item.socso else ZERO
            eis_extra += amount if item.eis else ZERO
            hrd_extra += amount if item.hrd else ZERO

    gross = sum((ln.amount for ln in b.lines if ln.kind == "earning"), ZERO)
    unpaid_amt = sum((ln.amount for ln in b.lines if ln.code == "UNPAID"), ZERO)
    ot_amt = sum((ln.amount for ln in b.lines if ln.code.startswith("OT_")), ZERO)

    # Statutory wages: OT counts for SOCSO/EIS but not EPF; pay items follow their company settings.
    category = employee.category
    epf_wage_normal = basic + unpaid_amt + epf_extra
    epf_wage = epf_wage_normal + epf_extra_ar
    socso_wage = basic + unpaid_amt + ot_amt + socso_extra
    eis_wage = basic + unpaid_amt + ot_amt + eis_extra

    age = _age_on(employee.date_of_birth, month_end)
    if age is None and category == "local":
        flag("warning", "no_date_of_birth", "No date of birth: EPF/SOCSO/EIS assume under 60")

    epf = statutory.epf(epf_wage, category, age)
    epf_normal = statutory.epf(epf_wage_normal, category, age)
    socso = statutory.socso(socso_wage, category, age, employee.socso_category)
    eis = statutory.eis(eis_wage, category, age, employee.eis_exempt)

    normal_taxable = basic + unpaid_amt + ot_amt + taxable_normal
    married_nonworking = employee.marital_status == "married" and not employee.spouse_working
    _, month = parse_period(period)
    pcb = statutory.pcb(
        month=month,
        resident=employee.tax_resident,
        married_nonworking_spouse=married_nonworking,
        children=employee.children,
        ytd_taxable=ytd.normal_taxable,
        ytd_epf=ytd.epf_employee,
        ytd_pcb=ytd.pcb,
        current_taxable=normal_taxable,
        current_epf=epf_normal.employee,
        additional_taxable=taxable_additional,
        additional_epf=epf.employee - epf_normal.employee,
        ytd_deductions=ytd.tp1_deductions,
        current_deductions=tp1,
        ytd_zakat=ytd.zakat,
        current_zakat=zakat,
    )

    b.add("deduction", "EPF_EE", "EPF (employee)", epf.employee, epf.trace)
    b.add("deduction", "SOCSO_EE", "SOCSO (employee)", socso.employee, socso.trace)
    b.add("deduction", "EIS_EE", "EIS (employee)", eis.employee, eis.trace)
    b.add("deduction", "PCB", "Income tax (PCB)", pcb.employee, pcb.trace)
    for code, name, amount, trace in later_deductions:
        b.add("deduction", code, name, amount, trace)
    b.add("employer", "EPF_ER", "EPF (employer)", epf.employer, epf.trace)
    b.add("employer", "SOCSO_ER", "SOCSO (employer)", socso.employer, socso.trace)
    b.add("employer", "EIS_ER", "EIS (employer)", eis.employer, eis.trace)
    if factory.statutory.hrd_levy_enabled and category == "local":
        levy_base = basic + unpaid_amt + hrd_extra
        b.add(
            "employer",
            "HRD",
            "HRD Corp levy",
            levy_base * factory.statutory.hrd_levy_rate,
            f"{factory.statutory.hrd_levy_rate:.0%} of RM{round_cents(levy_base)}",
        )

    deductions = sum((ln.amount for ln in b.lines if ln.kind == "deduction"), ZERO)
    employer = sum((ln.amount for ln in b.lines if ln.kind == "employer"), ZERO)
    net = gross - deductions
    if net < 0:
        flag("error", "negative_net", f"Net pay is negative (RM{net})")
    other_deductions = sum((amount for _, _, amount, _ in later_deductions), ZERO)
    if gross > 0 and other_deductions > gross / 2:
        flag("warning", "deductions_over_half", f"Non-statutory deductions RM{other_deductions} exceed 50% of "
             f"wages RM{round_cents(gross)}; check Employment Act s.24 limits")

    for label, expiry in (("Work permit", employee.permit_expiry), ("Passport", employee.passport_expiry)):
        if expiry and expiry <= month_end + timedelta(days=60):
            flag("warning", "document_expiring", f"{label} expires {expiry:%d/%m/%Y}")

    payslip = Payslip(
        employee_id=eid,
        employee_name=employee.name,
        department=employee.department,
        period=period,
        attendance_start=attendance_start,
        attendance_end=attendance_end,
        lines=b.lines,
        gross=round_cents(gross),
        total_deductions=round_cents(deductions),
        net=round_cents(net),
        employer_cost=round_cents(gross + employer),
        epf_wage=round_cents(epf_wage),
        socso_wage=round_cents(socso_wage),
        ot_hours_total=ot_hours_total,
        tax=TaxInfo(
            normal_taxable=round_cents(normal_taxable),
            additional_taxable=round_cents(taxable_additional),
            epf_employee_normal=epf_normal.employee,
            epf_employee_additional=epf.employee - epf_normal.employee,
            pcb=pcb.employee,
            tax_exempt=round_cents(tax_exempt),
            benefits_in_kind=round_cents(bik),
            tp1_deductions=round_cents(tp1),
            zakat=round_cents(zakat),
        ),
        days=days,
    )
    return payslip, flags
