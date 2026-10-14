from __future__ import annotations

import calendar
from datetime import date, timedelta

from hr_agent.config import PayrollCalendar


def parse_period(period: str) -> tuple[int, int]:
    year, month = period.split("-")
    return int(year), int(month)


def month_bounds(period: str) -> tuple[date, date]:
    y, m = parse_period(period)
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def attendance_bounds(period: str, cal: PayrollCalendar) -> tuple[date, date]:
    """Attendance/OT window ending on the cut-off day of the payroll month."""
    first, last = month_bounds(period)
    if cal.cutoff_day >= last.day:
        return first, last
    end = first.replace(day=cal.cutoff_day)
    prev_last = first - timedelta(days=1)
    start_day = min(cal.cutoff_day + 1, prev_last.day)
    start = prev_last.replace(day=start_day) if cal.cutoff_day < prev_last.day else first
    return start, end


def pay_date(period: str, cal: PayrollCalendar) -> date:
    y, m = parse_period(period)
    if cal.pay_in_following_month:
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return date(y, m, min(cal.pay_day, calendar.monthrange(y, m)[1]))


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)
