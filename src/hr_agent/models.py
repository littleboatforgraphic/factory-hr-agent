from __future__ import annotations

import datetime as dt
from datetime import datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from hr_agent.money import ZERO


class LeaveType(StrEnum):
    annual = "annual"
    medical = "medical"
    hospitalisation = "hospitalisation"
    unpaid = "unpaid"
    maternity = "maternity"
    paternity = "paternity"
    replacement = "replacement"
    emergency = "emergency"
    compassionate = "compassionate"


DEFAULT_LEAVE_PAID = {t.value: t is not LeaveType.unpaid for t in LeaveType}


class Employee(BaseModel):
    employee_id: str
    device_user_id: str = ""
    name: str
    id_type: Literal["ic", "passport"] = "ic"
    id_no: str = ""
    nationality: str = "Malaysian"
    category: Literal["local", "foreign"] = "local"
    tax_resident: bool = True
    date_of_birth: dt.date | None = None
    join_date: dt.date
    exit_date: dt.date | None = None
    department: str = ""
    position: str = ""
    schedule_id: str | None = None
    monthly_salary: Decimal
    ot_eligible: bool = True
    bank_name: str = ""
    bank_account: str = ""
    epf_no: str = ""
    socso_no: str = ""
    # "second" for employees who first joined SOCSO at 55+ with no prior contribution; "auto" picks by age.
    socso_category: Literal["auto", "first", "second"] = "auto"
    eis_exempt: bool = False  # e.g. first employed at 57+ with no prior EIS contribution
    tax_no: str = ""
    marital_status: Literal["single", "married"] = "single"
    spouse_working: bool = True
    children: int = 0
    language: str = "en"
    phone: str = ""
    telegram_id: str = ""
    permit_expiry: dt.date | None = None
    passport_expiry: dt.date | None = None

    @property
    def device_id(self) -> str:
        return self.device_user_id or self.employee_id


class PayItemEntry(BaseModel):
    """One row of the PayItems sheet: an amount of a company pay item for one employee."""

    employee_id: str
    item: str
    amount: Decimal
    period: str | None = None  # YYYY-MM for a one-off amount; blank = recurring between start and end
    start: dt.date | None = None
    end: dt.date | None = None
    approved_by: str = ""
    note: str = ""

    def applies_to(self, period: str, start: dt.date, end: dt.date) -> bool:
        if self.period:
            return self.period == period
        return (self.start is None or self.start <= end) and (self.end is None or self.end >= start)


class LeaveRecord(BaseModel):
    employee_id: str
    leave_type: LeaveType
    start: dt.date
    end: dt.date
    half_day: bool = False
    approved_by: str = ""


class LeaveBalance(BaseModel):
    """Optional per-employee leave settings for a year: entitlement override and days brought forward."""

    employee_id: str
    year: int
    leave_type: LeaveType
    entitled: Decimal | None = None
    carried_forward: Decimal = ZERO


class LeaveCredit(BaseModel):
    """Leave days granted to one employee, e.g. replacement leave for working a rest day or public holiday.
    Recorded by HR (or the agent on an HR officer's instruction) after the employee and manager confirm."""

    employee_id: str
    year: int
    leave_type: LeaveType = LeaveType.replacement
    days: Decimal = Field(gt=0)
    worked_on: dt.date | None = None
    reason: str = ""
    employee_confirmed: bool = False
    manager_confirmed_by: str = ""
    recorded_by: str = ""
    on_behalf_of: str = ""
    recorded_at: datetime | None = None


class LeaveSummaryRow(BaseModel):
    leave_type: LeaveType
    entitled: Decimal | None = None
    carried_forward: Decimal = ZERO
    taken_month: Decimal = ZERO
    taken_ytd: Decimal = ZERO
    balance: Decimal | None = None


class ChatLink(BaseModel):
    """A chat account (e.g. Telegram user id) linked to an employee after their phone number matched."""

    employee_id: str
    channel: Literal["telegram", "whatsapp"]
    chat_id: str
    linked_at: datetime | None = None
    linked_by: str = ""


class Punch(BaseModel):
    employee_id: str
    timestamp: datetime
    device_id: str = ""
    direction: Literal["in", "out", "unknown"] = "unknown"


class AttendanceOverride(BaseModel):
    employee_id: str
    date: dt.date
    check_in: time | None = None
    check_out: time | None = None
    reason: str
    by: str


Severity = Literal["info", "warning", "error", "block"]


class Flag(BaseModel):
    severity: Severity
    code: str
    message: str
    employee_id: str | None = None
    date: dt.date | None = None


class Line(BaseModel):
    kind: Literal["earning", "deduction", "employer", "benefit"]
    code: str
    label: str
    amount: Decimal
    quantity: Decimal | None = None
    rate: Decimal | None = None
    trace: str = ""


class DayResult(BaseModel):
    date: dt.date
    day_type: Literal["normal", "rest", "public_holiday"]
    status: Literal["present", "absent", "leave", "incomplete", "off", "not_employed"]
    scheduled_hours: Decimal = ZERO
    check_in: time | None = None
    check_out: time | None = None
    normal_hours: Decimal = ZERO
    ot_hours: Decimal = ZERO
    worked_hours: Decimal = ZERO
    late_minutes: int = 0
    leave_type: LeaveType | None = None
    leave_days: Decimal = ZERO
    overridden: bool = False


class TaxInfo(BaseModel):
    """Figures needed for PCB year-to-date and the EA form."""

    normal_taxable: Decimal = ZERO
    additional_taxable: Decimal = ZERO
    epf_employee_normal: Decimal = ZERO
    epf_employee_additional: Decimal = ZERO
    pcb: Decimal = ZERO
    tax_exempt: Decimal = ZERO  # EA Part F
    benefits_in_kind: Decimal = ZERO
    tp1_deductions: Decimal = ZERO
    zakat: Decimal = ZERO


class Payslip(BaseModel):
    employee_id: str
    employee_name: str
    period: str
    attendance_start: dt.date
    attendance_end: dt.date
    lines: list[Line]
    gross: Decimal
    total_deductions: Decimal
    net: Decimal
    employer_cost: Decimal
    epf_wage: Decimal
    socso_wage: Decimal
    ot_hours_total: Decimal
    tax: TaxInfo
    ytd: dict[str, Decimal] = Field(default_factory=dict)
    days: list[DayResult] = Field(default_factory=list)
    leave: list[LeaveSummaryRow] = Field(default_factory=list)


RunStatus = Literal["draft", "reviewed", "approved", "locked"]


class StatusChange(BaseModel):
    status: RunStatus
    by: str
    at: datetime
    note: str = ""


class PayrollRun(BaseModel):
    factory_id: str
    period: str
    version: int = 0
    status: RunStatus = "draft"
    created_at: datetime
    created_by: str
    payslips: list[Payslip]
    flags: list[Flag]
    history: list[StatusChange] = Field(default_factory=list)

    @property
    def blocking_flags(self) -> list[Flag]:
        return [f for f in self.flags if f.severity in ("error", "block")]
