"""Storage interface. The engine only talks to this, so backends are swappable."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from hr_agent.models import (
    AttendanceOverride,
    Employee,
    LeaveRecord,
    PayItemEntry,
    PayrollRun,
    Punch,
    RunStatus,
)


class RunLockedError(RuntimeError):
    pass


class Store(ABC):
    @abstractmethod
    def employees(self) -> list[Employee]: ...

    @abstractmethod
    def pay_items(self) -> list[PayItemEntry]:
        """All PayItems rows (recurring and one-off); the engine picks those that apply to a period."""

    @abstractmethod
    def leave(self, start: date, end: date) -> list[LeaveRecord]: ...

    @abstractmethod
    def overrides(self, start: date, end: date) -> list[AttendanceOverride]: ...

    @abstractmethod
    def save_punches(self, punches: list[Punch], source: str, user: str) -> int:
        """Append punches (already mapped to employee ids); duplicates are ignored."""

    @abstractmethod
    def punches(self, start: date, end: date) -> list[Punch]: ...

    @abstractmethod
    def save_run(self, run: PayrollRun) -> PayrollRun:
        """Persist a new run version. Raises RunLockedError if the period is locked."""

    @abstractmethod
    def load_run(self, period: str, version: int | None = None) -> PayrollRun | None: ...

    @abstractmethod
    def runs_for_year(self, year: int, locked_only: bool = True) -> list[PayrollRun]: ...

    @abstractmethod
    def set_status(self, period: str, version: int, status: RunStatus, user: str, note: str = "") -> PayrollRun: ...

    @abstractmethod
    def audit(self, action: str, user: str, details: str = "") -> None: ...
