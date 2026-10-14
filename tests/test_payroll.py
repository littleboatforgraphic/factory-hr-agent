from datetime import date
from decimal import Decimal

from conftest import emp, punches

from hr_agent.attendance import process_employee_attendance
from hr_agent.models import Bonus, LeaveRecord, LeaveType
from hr_agent.payroll import YearToDate, compute_payslip, public_holiday_pay, rest_day_pay
from hr_agent.statutory.my import MalaysiaStatutory


def statutory(factory):
    return MalaysiaStatutory(factory.resolve(factory.statutory.rates_file), factory.base_dir)


def full_month(eid, skip=()):
    pairs = []
    for day in range(1, 31):
        d = date(2026, 9, day)
        if d.weekday() == 6 or day in skip:
            continue
        end = "13:00" if d.weekday() == 5 else "17:00"
        pairs.append((f"{d}T08:00", f"{d}T{end}"))
    return punches(eid, *pairs)


def payslip(factory, employee, leave=(), bonuses=(), skip=()):
    start, end = date(2026, 9, 1), date(2026, 9, 30)
    ps = full_month(employee.employee_id, skip)
    days, _ = process_employee_attendance(employee, factory, ps, [], list(leave), set(), start, end)
    return compute_payslip(employee=employee, factory=factory, period="2026-09", days=days, attendance_start=start,
                           attendance_end=end, allowances=[], bonuses=list(bonuses), ytd=YearToDate(), statutory=statutory(factory))


def line(p, code):
    return next(ln.amount for ln in p.lines if ln.code == code)


def test_unpaid_leave_uses_calendar_days(factory):
    e = emp()
    lv = LeaveRecord(employee_id="F1", leave_type=LeaveType.unpaid, start=date(2026, 9, 21), end=date(2026, 9, 22))
    p, _ = payslip(factory, e, leave=[lv], skip=(21, 22))
    assert line(p, "UNPAID") == Decimal("-113.33")  # 1700 / 30 * 2


def test_rest_day_pays_higher_of_act_and_policy():
    orp, hrp = Decimal("100"), Decimal("12.5")
    assert rest_day_pay(Decimal("3"), orp, hrp, Decimal("8"), Decimal("2"))[0] == Decimal("75")  # policy 75 > 50
    amount, basis = rest_day_pay(Decimal("5"), orp, hrp, Decimal("8"), Decimal("1"))
    assert amount == Decimal("100") and "Employment Act" in basis


def test_public_holiday_minimum_is_two_days_wages():
    amount, basis = public_holiday_pay(Decimal("4"), Decimal("100"), Decimal("12.5"), Decimal("8"), Decimal("3"))
    assert amount == Decimal("200") and "Employment Act" in basis


def test_unapproved_bonus_excluded_and_flagged(factory):
    p, flags = payslip(factory, emp(), bonuses=[Bonus(employee_id="F1", period="2026-09", name="Incentive", amount=Decimal("100"))])
    assert not [ln for ln in p.lines if ln.code == "BONUS"]
    assert "bonus_not_approved" in {f.code for f in flags}


def test_foreign_worker_epf_and_no_eis(factory):
    p, _ = payslip(factory, emp())
    assert line(p, "EPF_EE") == Decimal("34.00")
    assert not [ln for ln in p.lines if ln.code in ("EIS_EE", "SOCSO_EE")]


def test_local_statutory_lines_and_net(factory):
    p, _ = payslip(factory, emp("L1", "3000", "local"))
    assert line(p, "EPF_EE") == Decimal("330.00") and line(p, "EPF_ER") == Decimal("390.00")
    assert p.net == p.gross - p.total_deductions


def test_below_minimum_wage_flag(factory):
    _, flags = payslip(factory, emp(salary="1500"))
    assert "below_minimum_wage" in {f.code for f in flags}


def test_pcb_progressive_and_nonresident(factory):
    s = statutory(factory)
    kw = dict(month=9, married_nonworking_spouse=False, children=0, ytd_taxable=Decimal("80000"),
              ytd_epf=Decimal("3000"), ytd_pcb=Decimal("3000"), current_taxable=Decimal("10000"), current_epf=Decimal("1100"))
    assert s.pcb(resident=True, **kw).employee > 0
    assert s.pcb(resident=False, **kw).employee == Decimal("3000.00")
