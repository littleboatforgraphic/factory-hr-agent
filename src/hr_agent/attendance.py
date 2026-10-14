"""Turn raw punches into per-day attendance results (normal hours, OT, status)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from hr_agent.config import WEEKDAYS, DaySchedule, FactoryConfig, OTPolicy, Schedule, TimeRange
from hr_agent.models import (
    DEFAULT_LEAVE_PAID,
    AttendanceOverride,
    DayResult,
    Employee,
    Flag,
    LeaveRecord,
    Punch,
)
from hr_agent.money import ZERO, ceil_to_step, floor_to_step
from hr_agent.periods import daterange


def round_ot_minutes(minutes: int, policy: OTPolicy) -> int:
    if minutes <= 0:
        return 0
    step = policy.block_minutes
    if policy.rounding == "floor":
        return int(floor_to_step(minutes, step))
    if policy.rounding == "ceil":
        return int(ceil_to_step(minutes, step))
    return int(floor_to_step(minutes + step / 2, step))


def _overlap_minutes(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> int:
    start, end = max(a_start, b_start), min(a_end, b_end)
    return max(0, int((end - start).total_seconds() // 60))


def _at(d: date, t: time) -> datetime:
    return datetime.combine(d, t)


def _hours(minutes: int) -> Decimal:
    return Decimal(minutes) / 60


def work_date(ts: datetime, boundary: time) -> date:
    return ts.date() - timedelta(days=1) if ts.time() < boundary else ts.date()


def group_punches(punches: list[Punch], boundary: time) -> dict[date, list[datetime]]:
    grouped: dict[date, list[datetime]] = defaultdict(list)
    for p in punches:
        grouped[work_date(p.timestamp, boundary)].append(p.timestamp)
    for times in grouped.values():
        times.sort()
    return grouped


def _leave_on(day: date, leave: list[LeaveRecord]) -> LeaveRecord | None:
    return next((rec for rec in leave if rec.start <= day <= rec.end), None)


def _reference_breaks(schedule: Schedule) -> list[TimeRange]:
    for wd in WEEKDAYS:
        if wd in schedule.days:
            return schedule.days[wd].breaks
    return []


def process_employee_attendance(
    employee: Employee,
    factory: FactoryConfig,
    punches: list[Punch],
    overrides: list[AttendanceOverride],
    leave: list[LeaveRecord],
    holidays: set[date],
    start: date,
    end: date,
) -> tuple[list[DayResult], list[Flag]]:
    schedule = factory.schedule(employee.schedule_id)
    policy = factory.ot
    grouped = group_punches(punches, factory.day_boundary)
    override_by_date = {o.date: o for o in overrides}
    leave_paid = {**DEFAULT_LEAVE_PAID, **factory.leave_paid}
    results: list[DayResult] = []
    flags: list[Flag] = []
    eid = employee.employee_id

    def flag(severity, code, message, d=None):
        flags.append(Flag(severity=severity, code=code, message=message, employee_id=eid, date=d))

    for d in daterange(start, end):
        weekday = WEEKDAYS[d.weekday()]
        day_sched: DaySchedule | None = schedule.days.get(weekday)
        if d in holidays:
            day_type = "public_holiday"
        elif day_sched is None:
            day_type = "rest"
        else:
            day_type = "normal"
        scheduled = day_sched.normal_hours if (day_sched and day_type == "normal") else ZERO

        if d < employee.join_date or (employee.exit_date and d > employee.exit_date):
            results.append(DayResult(date=d, day_type=day_type, status="not_employed"))
            continue

        times = grouped.get(d, [])
        check_in = times[0] if times else None
        check_out = times[-1] if len(times) > 1 else None
        overridden = False
        if d in override_by_date:
            o = override_by_date[d]
            overridden = True
            check_in = _at(d, o.check_in) if o.check_in else check_in
            if o.check_out:
                out_day = d + timedelta(days=1) if o.check_in and o.check_out < o.check_in else d
                check_out = _at(out_day, o.check_out)

        rec = _leave_on(d, leave)
        result = DayResult(
            date=d,
            day_type=day_type,
            status="off",
            scheduled_hours=scheduled,
            check_in=check_in.time() if check_in else None,
            check_out=check_out.time() if check_out else None,
            overridden=overridden,
        )

        if rec and day_type == "normal":
            result.leave_type = rec.leave_type
            result.leave_days = Decimal("0.5") if rec.half_day else Decimal("1")
            if not rec.half_day:
                result.status = "leave"
                if check_in:
                    flag("warning", "punch_on_leave", f"Punched in on {rec.leave_type.value} leave day", d)
                results.append(result)
                continue
            if not leave_paid.get(rec.leave_type.value, True):
                pass

        if check_in is None:
            if day_type == "normal" and not result.leave_days:
                result.status = "absent"
                flag("warning", "absent", "No punches and no leave recorded (treated as unpaid)", d)
            results.append(result)
            continue

        if check_out is None or check_out <= check_in:
            result.status = "incomplete"
            result.normal_hours = scheduled
            flag("error", "missing_punch", "Only one punch recorded; HR must enter the missing time", d)
            results.append(result)
            continue

        result.status = "present"
        if day_type == "normal" and day_sched is not None:
            normal_min = sum(
                _overlap_minutes(check_in, check_out, _at(d, s.start), _at(d, s.end))
                for s in day_sched.segments
            )
            result.normal_hours = _hours(normal_min)
            late = int((check_in - _at(d, day_sched.start)).total_seconds() // 60)
            if late > factory.late_grace_minutes:
                result.late_minutes = late
            end_dt = _at(d, day_sched.end)
            raw_ot = int((check_out - end_dt).total_seconds() // 60) if check_out > end_dt else 0
            if raw_ot and employee.ot_eligible:
                result.ot_hours = _hours(round_ot_minutes(raw_ot, policy))
            if check_out < end_dt and not result.leave_days:
                flag("info", "early_out", f"Left at {check_out:%H:%M}, before {day_sched.end:%H:%M}", d)
            result.worked_hours = result.normal_hours + result.ot_hours
        else:
            total = int((check_out - check_in).total_seconds() // 60)
            breaks = sum(
                _overlap_minutes(check_in, check_out, _at(d, b.start), _at(d, b.end))
                for b in _reference_breaks(schedule)
            )
            worked = round_ot_minutes(total - breaks, policy)
            result.worked_hours = _hours(worked)
            if not employee.ot_eligible:
                flag("warning", "non_ot_worked_off_day", f"Worked on {day_type.replace('_', ' ')} but not OT-eligible", d)
        results.append(result)

    _flag_consecutive_days(results, eid, flags)
    return results, flags


def _flag_consecutive_days(results: list[DayResult], eid: str, flags: list[Flag]) -> None:
    streak = 0
    for r in results:
        streak = streak + 1 if r.status in ("present", "incomplete") else 0
        if streak == 7:
            flags.append(
                Flag(
                    severity="warning",
                    code="no_rest_day",
                    message="Worked 7 consecutive days; Employment Act requires one rest day per week",
                    employee_id=eid,
                    date=r.date,
                )
            )
