"""Use-case layer shared by the CLI and the factory node API."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from hr_agent.attendance import process_employee_attendance
from hr_agent.config import FactoryConfig
from hr_agent.devices import load_profile, parse_export
from hr_agent.holidays import load_holidays
from hr_agent.leave import summarise_leave
from hr_agent.models import Flag, LeaveCredit, PayrollRun, Payslip, Punch, RunStatus
from hr_agent.money import ZERO
from hr_agent.payroll import YearToDate, compute_payslip
from hr_agent.periods import attendance_bounds, month_bounds, parse_period
from hr_agent.statutory.my import MalaysiaStatutory
from hr_agent.storage.base import Store
from hr_agent.storage.excel import punch_window

# Who may move a run into each status.
TRANSITIONS: dict[RunStatus, tuple[RunStatus, set[str]]] = {
    "reviewed": ("draft", {"accountant"}),
    "approved": ("reviewed", {"management"}),
    "locked": ("approved", {"accountant", "hr"}),
}


def import_attendance(factory: FactoryConfig, store: Store, file: str | Path, profile_id: str, user: str) -> dict:
    if profile_id not in factory.device_profiles:
        raise ValueError(f"Device profile {profile_id!r} is not enabled for factory {factory.id}")
    profile = load_profile(profile_id, factory.resolve("config/device_profiles"))
    raw, errors = parse_export(file, profile)
    by_device = {e.device_id: e.employee_id for e in store.employees()}
    mapped, unmapped = [], Counter()
    for p in raw:
        eid = by_device.get(p.employee_id)
        if eid is None:
            unmapped[p.employee_id] += 1
            continue
        mapped.append(Punch(employee_id=eid, timestamp=p.timestamp, direction=p.direction, device_id=p.device_id))
    added = store.save_punches(mapped, source=Path(file).name, user=user)
    return {
        "rows_read": len(raw),
        "punches_added": added,
        "duplicates_skipped": len(mapped) - added,
        "unmapped_device_ids": dict(unmapped),
        "errors": [e.model_dump() for e in errors],
    }


def _year_to_date(store: Store, period: str) -> tuple[dict[str, YearToDate], dict[str, Counter]]:
    """Totals from earlier locked runs this year: PCB inputs, and per-line amounts for the payslip YTD column."""
    year, month = parse_period(period)
    ytd: dict[str, YearToDate] = defaultdict(YearToDate)
    lines: dict[str, Counter] = defaultdict(Counter)
    for run in store.runs_for_year(year, locked_only=True):
        if parse_period(run.period)[1] >= month:
            continue
        for p in run.payslips:
            lines[p.employee_id].update(_line_totals(p))
            y = ytd[p.employee_id]
            y.normal_taxable += p.tax.normal_taxable + p.tax.additional_taxable
            y.epf_employee += p.tax.epf_employee_normal + p.tax.epf_employee_additional
            y.pcb += p.tax.pcb
            y.tp1_deductions += p.tax.tp1_deductions
            y.zakat += p.tax.zakat
    for eid, totals in lines.items():
        ytd[eid].items = dict(totals)
    return ytd, lines


def _line_totals(p: Payslip) -> Counter:
    c: Counter = Counter()
    for ln in p.lines:
        c[ln.code] += ln.amount
    c["GROSS"] += p.gross
    c["DEDUCTIONS"] += p.total_deductions
    c["NET"] += p.net
    return c


def run_payroll(factory: FactoryConfig, store: Store, period: str, user: str) -> PayrollRun:
    month_start, month_end = month_bounds(period)
    att_start, att_end = attendance_bounds(period, factory.calendar)
    calendar = load_holidays(factory.resolve(factory.holidays_file))
    statutory = MalaysiaStatutory(factory.resolve(factory.statutory.rates_file), factory.base_dir)
    flags: list[Flag] = []
    if not statutory.verified:
        flags.append(Flag(severity="warning", code="statutory_unverified", message="Statutory rates/tables are not verified against official sources"))
    if not calendar.verified:
        flags.append(Flag(severity="warning", code="holidays_unverified", message=f"Holiday calendar {calendar.state} {calendar.year} is not verified"))
    if calendar.year not in (att_start.year, att_end.year):
        flags.append(Flag(severity="error", code="holidays_wrong_year", message=f"Holiday file is for {calendar.year}"))

    employees = [
        e for e in store.employees()
        if e.join_date <= month_end and (e.exit_date is None or e.exit_date >= month_start)
    ]
    punches_by_emp: dict[str, list[Punch]] = defaultdict(list)
    for p in store.punches(*punch_window(att_start, att_end)):
        punches_by_emp[p.employee_id].append(p)
    year = parse_period(period)[0]
    leave = store.leave(min(date(year, 1, 1), att_start), att_end)
    balances = store.leave_balances(year)
    credits = store.leave_credits(year)
    overrides = store.overrides(att_start, att_end)
    pay_items = store.pay_items()
    ytd, ytd_lines = _year_to_date(store, period)

    payslips = []
    for emp in employees:
        eid = emp.employee_id
        days, f1 = process_employee_attendance(
            emp, factory, punches_by_emp.get(eid, []), [o for o in overrides if o.employee_id == eid],
            [rec for rec in leave if rec.employee_id == eid], calendar.dates(), att_start, att_end,
        )
        payslip, f2 = compute_payslip(
            employee=emp, factory=factory, period=period, days=days,
            attendance_start=att_start, attendance_end=att_end,
            pay_items=[i for i in pay_items if i.employee_id == eid],
            ytd=ytd.get(eid, YearToDate()), statutory=statutory,
        )
        totals = _line_totals(payslip)
        totals.update(ytd_lines.get(eid, Counter()))
        payslip.ytd = dict(totals)
        payslip.leave = summarise_leave(
            employee=emp, factory=factory, records=[rec for rec in leave if rec.employee_id == eid],
            balances=[b for b in balances if b.employee_id == eid], holidays=calendar.dates(), year=year,
            month_start=att_start, as_of=att_end, credits=[c for c in credits if c.employee_id == eid],
        )
        payslips.append(payslip)
        flags.extend(f1 + f2)

    accounts = Counter(e.bank_account for e in employees if e.bank_account)
    for acct, n in accounts.items():
        if n > 1:
            ids = ", ".join(e.employee_id for e in employees if e.bank_account == acct)
            flags.append(Flag(severity="error", code="duplicate_bank_account", message=f"Bank account shared by {ids}"))
    for e in employees:
        if not e.bank_account:
            flags.append(Flag(severity="warning", code="missing_bank_account", message="No bank account", employee_id=e.employee_id))

    run = PayrollRun(
        factory_id=factory.id, period=period, created_at=datetime.now(UTC).replace(microsecond=0),
        created_by=user, payslips=payslips, flags=flags,
    )
    return store.save_run(run)


def record_leave_credit(store: Store, credit: LeaveCredit, user: str, on_behalf_of: str = "") -> LeaveCredit:
    """Grant leave days (e.g. replacement leave) once the employee and their manager have confirmed."""
    if credit.employee_id not in {e.employee_id for e in store.employees()}:
        raise ValueError(f"Unknown employee {credit.employee_id!r}")
    if not credit.employee_confirmed or not credit.manager_confirmed_by.strip():
        raise ValueError("Confirm with the employee and name the approving manager before recording leave")
    if credit.days % Decimal("0.5"):
        raise ValueError("Leave is granted in half or full days")
    credit = credit.model_copy(update={"recorded_by": user, "on_behalf_of": on_behalf_of,
                                       "recorded_at": datetime.now().replace(microsecond=0)})
    return store.add_leave_credit(credit)


def transition(store: Store, period: str, version: int, target: RunStatus, user: str, role: str, note: str = "") -> PayrollRun:
    if target not in TRANSITIONS:
        raise ValueError(f"Cannot move a run to {target!r}")
    run = store.load_run(period, version)
    if run is None:
        raise FileNotFoundError(f"No payroll run {period} v{version}")
    required, roles = TRANSITIONS[target]
    if run.status != required:
        raise ValueError(f"Run is {run.status}; it must be {required} before {target}")
    if role not in roles:
        raise PermissionError(f"Role {role!r} cannot mark a run {target}")
    if target == "approved" and run.blocking_flags:
        raise ValueError(f"{len(run.blocking_flags)} blocking flag(s) must be resolved before approval")
    return store.set_status(period, version, target, user, note)


def run_summary(run: PayrollRun) -> dict:
    totals = {k: sum((getattr(p, k) for p in run.payslips), ZERO) for k in ("gross", "total_deductions", "net", "employer_cost", "ot_hours_total")}
    sev = Counter(f.severity for f in run.flags)
    return {
        "factory_id": run.factory_id, "period": run.period, "version": run.version, "status": run.status,
        "employees": len(run.payslips), "totals": {k: str(v) for k, v in totals.items()}, "flags_by_severity": dict(sev),
    }
