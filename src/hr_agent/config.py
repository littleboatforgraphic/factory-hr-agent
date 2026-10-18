"""Per-factory configuration. Nothing factory-specific is hard-coded elsewhere."""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from hr_agent.money import ZERO

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: list[str] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Employment Act 1955 s.60A normal-hours limits.
EA_MAX_DAILY_HOURS = Decimal("9")
EA_MAX_WEEKLY_HOURS = Decimal("45")


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


class TimeRange(BaseModel):
    start: time
    end: time

    @property
    def minutes(self) -> int:
        return _minutes(self.end) - _minutes(self.start)


class DaySchedule(BaseModel):
    segments: list[TimeRange]

    @property
    def normal_hours(self) -> Decimal:
        return Decimal(sum(s.minutes for s in self.segments)) / 60

    @property
    def start(self) -> time:
        return self.segments[0].start

    @property
    def end(self) -> time:
        return self.segments[-1].end

    @property
    def breaks(self) -> list[TimeRange]:
        return [
            TimeRange(start=a.end, end=b.start)
            for a, b in zip(self.segments, self.segments[1:], strict=False)
        ]


class Schedule(BaseModel):
    id: str
    name: str
    days: dict[Weekday, DaySchedule]

    @property
    def weekly_hours(self) -> Decimal:
        return sum((d.normal_hours for d in self.days.values()), ZERO)

    @model_validator(mode="after")
    def _check_employment_act(self) -> Schedule:
        for day, d in self.days.items():
            if d.normal_hours > EA_MAX_DAILY_HOURS:
                raise ValueError(
                    f"Schedule {self.id}: {day} has {d.normal_hours} normal hours; "
                    f"Employment Act allows at most {EA_MAX_DAILY_HOURS}"
                )
        if self.weekly_hours > EA_MAX_WEEKLY_HOURS:
            raise ValueError(
                f"Schedule {self.id}: {self.weekly_hours} normal hours/week; "
                f"Employment Act allows at most {EA_MAX_WEEKLY_HOURS}"
            )
        return self


class OTPolicy(BaseModel):
    block_minutes: int = 30
    rounding: Literal["floor", "nearest", "ceil"] = "floor"
    normal_day_rate: Decimal = Decimal("1.5")
    rest_day_rate: Decimal = Decimal("2.0")
    public_holiday_rate: Decimal = Decimal("3.0")
    monthly_cap_hours: Decimal = Decimal("104")
    warn_at_hours: Decimal = Decimal("80")
    orp_divisor_days: Decimal = Decimal("26")
    hourly_divisor_hours: Decimal = Decimal("8")


class PayrollCalendar(BaseModel):
    """cutoff_day >= days in month means the attendance period is the calendar month."""

    cutoff_day: int = Field(31, ge=1, le=31)
    pay_day: int = Field(7, ge=1, le=31)
    pay_in_following_month: bool = True


class StorageConfig(BaseModel):
    backend: Literal["excel", "sqlite", "postgres"] = "excel"
    path: str = "data"


class BankConfig(BaseModel):
    bank_code: str = "HLB"
    bank_name: str = "Hong Leong Bank"
    company_account: str = ""
    file_format: str = "hlb_bulk"


class StatutoryOptions(BaseModel):
    rates_file: str = "config/statutory/my-2026.yaml"
    hrd_levy_enabled: bool = True
    hrd_levy_rate: Decimal = Decimal("0.01")


class Employer(BaseModel):
    legal_name: str
    registration_no: str = ""
    address: str = ""
    epf_no: str = ""
    socso_no: str = ""
    tax_no: str = ""
    logo_file: str | None = None


class FactoryConfig(BaseModel):
    id: str
    name: str
    employer: Employer
    country: Literal["MY", "SG"] = "MY"
    state: str
    holidays_file: str
    storage: StorageConfig = StorageConfig()
    bank: BankConfig = BankConfig()
    calendar: PayrollCalendar = PayrollCalendar()
    ot: OTPolicy = OTPolicy()
    statutory: StatutoryOptions = StatutoryOptions()
    schedules: list[Schedule]
    default_schedule: str
    unpaid_divisor: Literal["calendar_days", "working_days", "fixed_26"] = "calendar_days"
    day_boundary: time = time(4, 0)
    late_grace_minutes: int = 0
    minimum_wage_monthly: Decimal = Decimal("1700")
    leave_paid: dict[str, bool] = Field(default_factory=dict)
    device_profiles: list[str] = Field(default_factory=lambda: ["generic_csv"])
    payslip_languages: list[str] = Field(default_factory=lambda: ["en"])
    base_dir: Path = Path(".")

    @field_validator("country")
    @classmethod
    def _only_malaysia_for_now(cls, v: str) -> str:
        if v != "MY":
            raise ValueError("Only the Malaysia rule pack is implemented (SG is planned)")
        return v

    @model_validator(mode="after")
    def _default_schedule_exists(self) -> FactoryConfig:
        if self.default_schedule not in {s.id for s in self.schedules}:
            raise ValueError(f"default_schedule {self.default_schedule!r} is not defined")
        return self

    def schedule(self, schedule_id: str | None) -> Schedule:
        sid = schedule_id or self.default_schedule
        for s in self.schedules:
            if s.id == sid:
                return s
        raise KeyError(f"Unknown schedule {sid!r}")

    def resolve(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else (self.base_dir / p)


def load_factory(path: str | Path, base_dir: str | Path | None = None) -> FactoryConfig:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["base_dir"] = Path(base_dir) if base_dir else _project_root(path)
    return FactoryConfig.model_validate(data)


def _project_root(config_path: Path) -> Path:
    for parent in config_path.resolve().parents:
        if (parent / "config").is_dir():
            return parent
    return config_path.resolve().parent
