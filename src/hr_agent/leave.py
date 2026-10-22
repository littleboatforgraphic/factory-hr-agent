"""Leave entitlement, usage and balance per leave type, for the payslip leave table."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from hr_agent.config import EA_LEAVE_MINIMUMS, WEEKDAYS, FactoryConfig, LeaveEntitlement
from hr_agent.models import Employee, LeaveBalance, LeaveRecord, LeaveSummaryRow, LeaveType
from hr_agent.money import ZERO

ALWAYS_SHOWN = (LeaveType.annual, LeaveType.medical, LeaveType.hospitalisation)


def _service_years(join: date, on: date) -> int:
    return max(on.year - join.year - ((on.month, on.day) < (join.month, join.day)), 0)


def entitlement(employee: Employee, factory: FactoryConfig, leave_type: str, year: int, on: date,
                override: Decimal | None = None) -> Decimal | None:
    """Company or employee entitlement, never below the Employment Act minimum."""
    years = _service_years(employee.join_date, on)
    ea = EA_LEAVE_MINIMUMS.get(leave_type)
    company = factory.leave_entitlements.get(leave_type)
    candidates = [r.days_for(years) for r in (ea, company) if r] + ([override] if override is not None else [])
    candidates = [c for c in candidates if c is not None]
    if not candidates:
        return None
    days = max(candidates)
    rule = company or ea
    if rule and rule.prorate_first_year and employee.join_date.year == year and override is None:
        # s.60E(1A): proportion of completed months; a fraction of half a day or more counts as a full day.
        months = 12 - employee.join_date.month + (1 if employee.join_date.day == 1 else 0)
        days = (days * months / 12).quantize(Decimal(1), ROUND_HALF_UP)
    return days


def _days_taken(rec: LeaveRecord, start: date, end: date, rule: LeaveEntitlement | None,
                employee: Employee, factory: FactoryConfig, holidays: set[date]) -> Decimal:
    schedule = factory.schedule(employee.schedule_id)
    calendar_days = bool(rule and rule.count == "calendar_days")
    total, d = ZERO, max(rec.start, start)
    while d <= min(rec.end, end):
        if calendar_days or (WEEKDAYS[d.weekday()] in schedule.days and d not in holidays):
            total += Decimal("0.5") if rec.half_day else Decimal(1)
        d += timedelta(days=1)
    return total


def summarise_leave(
    *,
    employee: Employee,
    factory: FactoryConfig,
    records: list[LeaveRecord],
    balances: list[LeaveBalance],
    holidays: set[date],
    year: int,
    month_start: date,
    as_of: date,
) -> list[LeaveSummaryRow]:
    year_start = date(year, 1, 1)
    by_type = {b.leave_type: b for b in balances if b.year == year}
    taken: dict[LeaveType, tuple[Decimal, Decimal]] = {}
    for t in LeaveType:
        rule = factory.leave_entitlements.get(t.value) or EA_LEAVE_MINIMUMS.get(t.value)
        recs = [r for r in records if r.leave_type is t]
        month = sum((_days_taken(r, month_start, as_of, rule, employee, factory, holidays) for r in recs), ZERO)
        ytd = sum((_days_taken(r, year_start, as_of, rule, employee, factory, holidays) for r in recs), ZERO)
        taken[t] = (month, ytd)

    rows = []
    for t in LeaveType:
        bal = by_type.get(t)
        month, ytd = taken[t]
        shown = t in ALWAYS_SHOWN or ytd > 0 or bal is not None or t.value in factory.leave_entitlements
        if not shown:
            continue
        entitled = None if t is LeaveType.unpaid else entitlement(
            employee, factory, t.value, year, as_of, bal.entitled if bal else None)
        cf = bal.carried_forward if bal else ZERO
        used = ytd + (taken[LeaveType.medical][1] if t is LeaveType.hospitalisation else ZERO)  # s.60F: 60 days incl. sick leave
        balance = None if entitled is None else entitled + cf - used
        rows.append(LeaveSummaryRow(leave_type=t, entitled=entitled, carried_forward=cf, taken_month=month,
                                    taken_ytd=ytd, balance=balance))
    return rows
