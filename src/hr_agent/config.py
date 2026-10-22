"""Per-factory configuration. Nothing factory-specific is hard-coded elsewhere."""

from __future__ import annotations

import re
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


# Line codes the engine itself produces; pay items may not reuse them.
RESERVED_CODES = {
    "BASIC", "UNPAID", "PCB", "EPF_EE", "EPF_ER", "SOCSO_EE", "SOCSO_ER", "EIS_EE", "EIS_ER", "HRD",
    "GROSS", "DEDUCTIONS", "NET",
}
_CODE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class PayItem(BaseModel):
    """A company-defined allowance, incentive, benefit, employer contribution or deduction."""

    name: str
    type: Literal["earning", "benefit_in_kind", "employer_contribution", "deduction"] = "earning"
    labels: dict[str, str] = Field(default_factory=dict)
    epf: bool = False
    socso: bool = False
    eis: bool = False
    hrd: bool = False
    tax: Literal["normal", "additional", "exempt"] = "normal"
    tax_exempt_annual_limit: Decimal | None = None
    tp1_relief_annual_cap: Decimal | None = None
    zakat: bool = False
    requires_approval: bool = False
    note: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> PayItem:
        if self.type != "earning" and (self.epf or self.socso or self.eis or self.hrd):
            raise ValueError(f"{self.name}: only earnings can count as EPF/SOCSO/EIS/HRD wages")
        if self.type != "deduction" and (self.zakat or self.tp1_relief_annual_cap is not None):
            raise ValueError(f"{self.name}: zakat / tp1_relief_annual_cap apply to deductions only")
        if self.tax_exempt_annual_limit is not None and self.tax != "exempt":
            raise ValueError(f"{self.name}: tax_exempt_annual_limit needs tax: exempt")
        if self.tax == "additional" and self.type != "earning":
            raise ValueError(f"{self.name}: only earnings can be additional remuneration")
        return self


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
    pay_items_file: str | None = None
    pay_items: dict[str, PayItem] = Field(default_factory=dict)
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

    @field_validator("pay_items")
    @classmethod
    def _pay_item_codes(cls, items: dict[str, PayItem]) -> dict[str, PayItem]:
        for code in items:
            if not _CODE.match(code) or code in RESERVED_CODES or code.startswith("OT_"):
                raise ValueError(f"Pay item code {code!r} must be UPPER_CASE and not a reserved line code")
        return items

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
    data["pay_items"] = _merge_pay_items(data)
    return FactoryConfig.model_validate(data)


def _merge_pay_items(data: dict) -> dict:
    """Defaults from pay_items_file, then the factory's own items/overrides; CODE: null removes a default."""
    items: dict = {}
    if data.get("pay_items_file"):
        f = Path(data["pay_items_file"])
        f = f if f.is_absolute() else data["base_dir"] / f
        items = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    for code, override in (data.get("pay_items") or {}).items():
        if override is None:
            items.pop(code, None)
        else:
            items[code] = {**(items.get(code) or {}), **override}
    return items


def _project_root(config_path: Path) -> Path:
    for parent in config_path.resolve().parents:
        if (parent / "config").is_dir():
            return parent
    return config_path.resolve().parent
