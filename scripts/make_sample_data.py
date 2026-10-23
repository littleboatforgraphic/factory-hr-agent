"""Generate fictional demo data: master.xlsx + a ZKTeco-style CSV for September 2026."""

from __future__ import annotations

import csv
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hr_agent.config import load_factory  # noqa: E402
from hr_agent.storage.excel import MASTER_SHEETS, ExcelStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
random.seed(7)

EMPLOYEES = [
    # id, name, category, nationality, salary, language, marital, spouse_working, children, dob
    ("E001", "Tan Mei Ling", "local", "Malaysian", 3200, "zh", "married", False, 2, date(1985, 4, 2)),
    ("E002", "Ahmad Faizal bin Ismail", "local", "Malaysian", 2600, "ms", "single", True, 0, date(1994, 8, 19)),
    ("E003", "Siti Nur Aisyah", "local", "Malaysian", 2200, "ms", "married", True, 1, date(1990, 1, 11)),
    ("F101", "Md Rahim Uddin", "foreign", "Bangladeshi", 1700, "bn", "married", True, 2, date(1992, 3, 5)),
    ("F102", "Kamal Hossain", "foreign", "Bangladeshi", 1700, "bn", "single", True, 0, date(1996, 6, 23)),
    ("F103", "Aung Kyaw Min", "foreign", "Myanmar", 1750, "my", "single", True, 0, date(1995, 12, 1)),
    ("F104", "Thida Win", "foreign", "Myanmar", 1700, "my", "single", True, 0, date(1998, 9, 9)),
    ("F105", "Ram Bahadur Thapa", "foreign", "Nepali", 1800, "ne", "married", True, 1, date(1989, 2, 14)),
    ("F106", "Sunil Gurung", "foreign", "Nepali", 1700, "ne", "single", True, 0, date(1999, 7, 30)),
    ("F107", "Arif Hasan", "foreign", "Bangladeshi", 1700, "bn", "single", True, 0, date(1997, 11, 17)),
]


def main() -> None:
    factory = load_factory(ROOT / "config/factories/demo-pahang.yaml")
    store = ExcelStore(factory.resolve(factory.storage.path))
    if store.master.exists():
        store.master.unlink()
    store.init_master()
    wb = load_workbook(store.master)
    ws = wb["Employees"]
    cols = MASTER_SHEETS["Employees"]
    for i, (eid, name, cat, nat, sal, lang, ms, sw, kids, dob) in enumerate(EMPLOYEES):
        row = dict(
            employee_id=eid, device_user_id=str(100 + i), name=name, id_type="ic" if cat == "local" else "passport",
            id_no=f"{'900101-06-' if cat == 'local' else 'P'}{1000 + i}", nationality=nat, category=cat,
            tax_resident="Y", date_of_birth=dob, join_date=date(2026, 9, 14) if eid == "F107" else date(2022, 1, 3),
            department="Production" if cat == "foreign" else "Admin", position="Operator" if cat == "foreign" else "Executive",
            monthly_salary=sal, ot_eligible="Y", bank_name="Hong Leong Bank", bank_account=f"0{2000000000 + i}",
            epf_no=f"{30000000 + i}", socso_no=f"S{4000 + i}", marital_status=ms, spouse_working="Y" if sw else "N",
            children=kids, language=lang, permit_expiry=date(2026, 10, 20) if eid == "F104" else None,
        )
        ws.append([row.get(c) for c in cols])
    for item in (
        {"employee_id": "E001", "item": "FIXED_ALW", "amount": 300, "note": "Position allowance"},
        {"employee_id": "F105", "item": "SKILL_ALW", "amount": 100},
        {"employee_id": "F103", "item": "INCENTIVE", "period": "2026-09", "amount": 150, "approved_by": "GM Lim"},
        {"employee_id": "F106", "item": "INCENTIVE", "period": "2026-09", "amount": 100},
        {"employee_id": "F101", "item": "HOSTEL", "amount": 50, "approved_by": "Consent form 2026-01"},
    ):
        wb["PayItems"].append([item.get(c) for c in MASTER_SHEETS["PayItems"]])
    for lv in (
        {"employee_id": "E003", "leave_type": "annual", "start": date(2026, 9, 7), "end": date(2026, 9, 8), "approved_by": "HR"},
        {"employee_id": "F102", "leave_type": "unpaid", "start": date(2026, 9, 21), "end": date(2026, 9, 22), "approved_by": "HR"},
        {"employee_id": "F101", "leave_type": "medical", "start": date(2026, 9, 10), "end": date(2026, 9, 10), "approved_by": "HR"},
    ):
        wb["Leave"].append([lv.get(c) for c in MASTER_SHEETS["Leave"]])
    for bal in (
        {"employee_id": "E003", "year": 2026, "leave_type": "annual", "carried_forward": 3},
        {"employee_id": "F101", "year": 2026, "leave_type": "replacement", "entitled": 1},
    ):
        wb["LeaveBalances"].append([bal.get(c) for c in MASTER_SHEETS["LeaveBalances"]])
    wb.save(store.master)

    out = ROOT / "sample_data" / "zkteco_2026-09.csv"
    out.parent.mkdir(exist_ok=True)
    leave_days = {("E003", 7), ("E003", 8), ("F102", 21), ("F102", 22), ("F101", 10)}
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["AC-No.", "Name", "Time", "State"])
        for i, (eid, name, cat, *_rest) in enumerate(EMPLOYEES):
            dev = str(100 + i)
            for day in range(1, 31):
                d = date(2026, 9, day)
                if eid == "F107" and day < 14:
                    continue
                wd = d.weekday()
                if (eid, day) in leave_days:
                    continue
                if eid == "F106" and day == 3:
                    continue  # absent without leave
                worked_holiday = d == date(2026, 9, 16) and cat == "foreign" and eid in ("F101", "F103")
                worked_sunday = wd == 6 and eid == "F105" and day in (6, 20)
                if wd == 6 and not worked_sunday:
                    continue
                if d == date(2026, 9, 16) and not worked_holiday:
                    continue
                cin = datetime.combine(d, time(7, 50)) + timedelta(minutes=random.randint(0, 12))
                end = time(13, 0) if wd == 5 else time(17, 0)
                extra = 0
                if cat == "foreign" and wd < 5:
                    extra = random.choice([0, 0, 47, 65, 95, 125, 185])
                if eid == "F101" and wd < 5:
                    extra = random.choice([185, 215, 245])  # heavy OT -> near cap flag
                if worked_holiday or worked_sunday:
                    end, extra = time(17, 0), 60
                cout = datetime.combine(d, end) + timedelta(minutes=extra + random.randint(0, 6))
                w.writerow([dev, name, cin.strftime("%d/%m/%Y %H:%M:%S"), "C/In"])
                if not (eid == "F104" and day == 24):  # missing clock-out
                    w.writerow([dev, name, cout.strftime("%d/%m/%Y %H:%M:%S"), "C/Out"])
    print(store.master)
    print(out)


if __name__ == "__main__":
    main()
