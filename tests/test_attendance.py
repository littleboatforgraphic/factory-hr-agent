from datetime import date
from decimal import Decimal

from conftest import emp, punches

from hr_agent.attendance import process_employee_attendance, round_ot_minutes
from hr_agent.config import OTPolicy
from hr_agent.models import LeaveRecord, LeaveType


def run(factory, ps, start, end=None, leave=(), holidays=()):
    return process_employee_attendance(emp(), factory, ps, [], list(leave), set(holidays), start, end or start)


def test_ot_rounds_down_to_30_minute_blocks():
    p = OTPolicy()
    assert round_ot_minutes(29, p) == 0
    assert round_ot_minutes(59, p) == 30
    assert round_ot_minutes(107, p) == 90


def test_weekday_ot_after_scheduled_end(factory):
    # Tue 2026-09-01: scheduled 08:00-12:00, 13:00-17:00
    days, flags = run(factory, punches("F1", ("2026-09-01T07:55", "2026-09-01T18:47")), date(2026, 9, 1))
    d = days[0]
    assert d.normal_hours == Decimal("8") and d.ot_hours == Decimal("1.5")
    assert not [f for f in flags if f.severity in ("error", "block")]


def test_saturday_five_hours_then_ot(factory):
    days, _ = run(factory, punches("F1", ("2026-09-05T08:00", "2026-09-05T13:40")), date(2026, 9, 5))
    assert days[0].normal_hours == Decimal("5") and days[0].ot_hours == Decimal("0.5")


def test_sunday_is_rest_day_and_holiday_detected(factory):
    days, _ = run(factory, punches("F1", ("2026-09-06T08:00", "2026-09-06T12:00")), date(2026, 9, 6))
    assert days[0].day_type == "rest" and days[0].worked_hours == Decimal("4")
    days, _ = run(factory, [], date(2026, 9, 16), holidays=[date(2026, 9, 16)])
    assert days[0].day_type == "public_holiday" and days[0].status != "absent"


def test_missing_punch_and_absence_flags(factory):
    ps = punches("F1", ("2026-09-01T08:00", "2026-09-01T17:00"))[:1]
    days, flags = run(factory, ps, date(2026, 9, 1), date(2026, 9, 2))
    codes = {f.code for f in flags}
    assert "missing_punch" in codes and "absent" in codes
    assert days[1].status == "absent"


def test_leave_day_not_absent(factory):
    lv = LeaveRecord(employee_id="F1", leave_type=LeaveType.unpaid, start=date(2026, 9, 1), end=date(2026, 9, 1))
    days, _ = run(factory, [], date(2026, 9, 1), leave=[lv])
    assert days[0].status == "leave" and days[0].leave_days == 1
