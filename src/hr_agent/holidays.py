from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel


class Holiday(BaseModel):
    date: date
    name: str


class HolidayCalendar(BaseModel):
    country: str
    state: str
    year: int
    verified: bool = False
    holidays: list[Holiday]

    def dates(self) -> set[date]:
        return {h.date for h in self.holidays}

    def name_of(self, d: date) -> str | None:
        return next((h.name for h in self.holidays if h.date == d), None)


def load_holidays(path: str | Path) -> HolidayCalendar:
    return HolidayCalendar.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
