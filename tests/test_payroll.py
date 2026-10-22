from datetime import date
from decimal import Decimal

from conftest import emp, punches

from hr_agent.attendance import process_employee_attendance
from hr_agent.models import LeaveRecord, LeaveType, PayItemEntry
from hr_agent.money import ZERO
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


def payslip(factory, employee, leave=(), items=(), skip=(), ytd=None):
    start, end = date(2026, 9, 1), date(2026, 9, 30)
    ps = full_month(employee.employee_id, skip)
    days, _ = process_employee_attendance(employee, factory, ps, [], list(leave), set(), start, end)
    return compute_payslip(employee=employee, factory=factory, period="2026-09", days=days, attendance_start=start,
                           attendance_end=end, pay_items=list(items), ytd=ytd or YearToDate(), statutory=statutory(factory))


def item(eid, code, amount, **kw):
    return PayItemEntry(employee_id=eid, item=code, amount=Decimal(amount), **kw)


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


def test_unapproved_incentive_excluded_and_flagged(factory):
    p, flags = payslip(factory, emp(), items=[item("F1", "INCENTIVE", "100", period="2026-09")])
    assert not [ln for ln in p.lines if ln.code == "INCENTIVE"]
    assert "pay_item_not_approved" in {f.code for f in flags}
    p, _ = payslip(factory, emp(), items=[item("F1", "INCENTIVE", "100", period="2026-09", approved_by="GM")])
    assert line(p, "INCENTIVE") == 100 and p.tax.additional_taxable == 100
    p, _ = payslip(factory, emp(), items=[item("F1", "INCENTIVE", "100", period="2026-10", approved_by="GM")])
    assert not [ln for ln in p.lines if ln.code == "INCENTIVE"]


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


def test_epf_bands_and_employer_rate_threshold(factory):
    s = statutory(factory)
    assert (s.epf(Decimal("1700"), "local").employee, s.epf(Decimal("1700"), "local").employer) == (187, 221)
    at5k = s.epf(Decimal("5000"), "local")
    assert (at5k.employee, at5k.employer) == (550, 650)
    above5k = s.epf(Decimal("5000.01"), "local")
    assert (above5k.employee, above5k.employer) == (561, 612)
    big = s.epf(Decimal("25000.50"), "local")
    assert (big.employee, big.employer) == (2751, 3001)


def test_epf_citizen_60_plus_employer_only(factory):
    s = statutory(factory)
    c = s.epf(Decimal("3000"), "local", age=61)
    assert (c.employee, c.employer) == (0, 120)
    f = s.epf(Decimal("1700"), "foreign", age=61)
    assert (f.employee, f.employer) == (34, 34)
    e = emp("L60", "3000", "local").model_copy(update={"date_of_birth": date(1960, 1, 1)})
    p, _ = payslip(factory, e)
    assert not [ln for ln in p.lines if ln.code == "EPF_EE" and ln.amount]
    assert line(p, "EPF_ER") == 120


def test_socso_official_tables_by_category(factory):
    s = statutory(factory)
    c1 = s.socso(Decimal("1700"), "local", age=40)
    assert (c1.employee, c1.employer) == (Decimal("20.60"), Decimal("28.85"))
    c1b = s.socso(Decimal("1700.01"), "local", age=40)
    assert (c1b.employee, c1b.employer) == (Decimal("21.90"), Decimal("30.65"))
    c2 = s.socso(Decimal("1750"), "local", age=61)
    assert (c2.employee, c2.employer) == (Decimal("13.15"), Decimal("21.90"))
    joined_late = s.socso(Decimal("1750"), "local", age=56, override="second")
    assert joined_late.employee == Decimal("13.15")
    capped = s.socso(Decimal("9000"), "local", age=30)
    assert (capped.employee, capped.employer) == (Decimal("74.40"), Decimal("104.15"))
    fw = s.socso(Decimal("1750"), "foreign", age=30)
    assert (fw.employee, fw.employer) == (Decimal("0.00"), Decimal("35.05"))
    assert all(s.uses_official_tables()[k] for k in ("socso_first", "socso_second", "socso_foreign", "eis_local"))


def test_eis_official_table_and_age_limits(factory):
    s = statutory(factory)
    assert s.eis(Decimal("1700"), "local", age=30).employee == Decimal("3.30")
    assert s.eis(Decimal("1750"), "local", age=30).employer == Decimal("3.50")
    assert s.eis(Decimal("8000"), "local", age=30).employee == Decimal("11.90")
    assert s.eis(Decimal("3000"), "local", age=60).employee == 0
    assert s.eis(Decimal("3000"), "local", age=17).employee == 0
    assert s.eis(Decimal("3000"), "local", age=30, exempt=True).employee == 0
    assert s.eis(Decimal("3000"), "foreign", age=30).employee == 0


def test_local_60_plus_payslip_uses_socso_second_category_and_no_eis(factory):
    e = emp("L61", "3000", "local").model_copy(update={"date_of_birth": date(1965, 1, 1)})
    p, _ = payslip(factory, e)
    assert line(p, "SOCSO_EE") == Decimal("22.15") and line(p, "SOCSO_ER") == Decimal("36.90")
    assert not [ln for ln in p.lines if ln.code == "EIS_EE"]


# Worked example from LHDN "Specification for MTD calculations using computerized calculation for 2026",
# Exhibit 5: married, spouse working, 3 children, RM5,500/month, EPF RM605, bonus RM8,250 (EPF RM908) in April.
def test_pcb_matches_lhdn_2026_worked_example(factory):
    s = statutory(factory)
    base = dict(resident=True, married_nonworking_spouse=False, children=3,
                current_taxable=Decimal("5500"), current_epf=Decimal("605"))

    def mtd(month, ytd_pcb, paid_months, **kw):
        return s.pcb(month=month, ytd_taxable=Decimal("5500") * paid_months, ytd_epf=Decimal("605") * paid_months,
                     ytd_pcb=Decimal(ytd_pcb), **base, **kw).employee

    assert mtd(1, "0", 0) == Decimal("110.00")
    assert mtd(2, "110", 1) == Decimal("110.00")
    assert mtd(3, "220", 2, current_deductions=Decimal("300")) == Decimal("108.20")
    april = mtd(4, "328.20", 3, ytd_deductions=Decimal("300"), current_deductions=Decimal("300"),
                additional_taxable=Decimal("8250"), additional_epf=Decimal("908"))
    assert april == Decimal("106.20") + Decimal("727.50")


def test_pcb_below_ten_ringgit_not_deducted(factory):
    s = statutory(factory)
    c = s.pcb(month=1, resident=True, married_nonworking_spouse=False, children=0, ytd_taxable=ZERO, ytd_epf=ZERO,
              ytd_pcb=ZERO, current_taxable=Decimal("3000"), current_epf=Decimal("330"))
    assert c.employee == 0 and "not deducted" in c.trace


def with_items(factory, **items):
    from hr_agent.config import PayItem
    return factory.model_copy(update={"pay_items": {**factory.pay_items, **{k: PayItem(**v) for k, v in items.items()}}})


def test_pay_item_switches_drive_each_statutory_wage(factory):
    f = with_items(factory, X_ALW={"name": "SOCSO-only allowance", "socso": True, "tax": "exempt"})
    p, _ = payslip(f, emp("L1", "3000", "local"), items=[item("L1", "X_ALW", "200")])
    assert p.gross == 3200 and p.epf_wage == 3000 and p.socso_wage == 3200
    assert p.tax.normal_taxable == 3000 and p.tax.tax_exempt == 200  # tax switch respected
    assert line(p, "EIS_EE") == statutory(f).eis(Decimal("3000"), "local", 30).employee  # EIS switch respected
    assert line(p, "HRD") == Decimal("30.00")


def test_tax_exempt_annual_limit_taxes_the_excess(factory):
    ytd = YearToDate(items={"TRAVEL_ALW": Decimal("5800")})
    p, _ = payslip(factory, emp("L1", "3000", "local"), items=[item("L1", "TRAVEL_ALW", "500")], ytd=ytd)
    assert p.tax.tax_exempt == 200 and p.tax.normal_taxable == 3300
    assert p.epf_wage == 3000 and p.socso_wage == 3000


def test_benefit_in_kind_is_taxed_but_not_paid(factory):
    from hr_agent.payslip import render_payslip_html
    e = emp("L1", "3000", "local")
    p, _ = payslip(factory, e, items=[item("L1", "VOLA", "500")])
    assert p.gross == 3000 and p.tax.normal_taxable == 3500 and p.tax.benefits_in_kind == 500
    assert p.net == p.gross - p.total_deductions
    assert "Living accommodation" not in render_payslip_html(p, e, factory)


def test_prs_employer_and_employee_with_tp1_cap(factory):
    ytd = YearToDate(items={"PRS_EE": Decimal("2900")})
    p, _ = payslip(factory, emp("L1", "6000", "local"),
                   items=[item("L1", "PRS_ER", "300"), item("L1", "PRS_EE", "200")], ytd=ytd)
    assert line(p, "PRS_ER") == 300 and p.employer_cost > p.gross + 300
    assert p.tax.normal_taxable == 6000 and p.tax.tp1_deductions == 100
    assert line(p, "PRS_EE") == 200 and p.net == p.gross - p.total_deductions


def test_zakat_reduces_pcb(factory):
    e = emp("L1", "8000", "local")
    ytd = YearToDate(normal_taxable=Decimal("64000"), epf_employee=Decimal("3520"))
    base, _ = payslip(factory, e, ytd=ytd)
    p, _ = payslip(factory, e, items=[item("L1", "ZAKAT", "50")], ytd=ytd)
    assert line(p, "ZAKAT") == 50 and line(p, "PCB") == line(base, "PCB") - 50 and p.tax.zakat == 50
    s = statutory(factory)
    kw = dict(month=5, resident=True, married_nonworking_spouse=False, children=0, ytd_taxable=Decimal("32000"),
              ytd_epf=Decimal("3520"), current_taxable=Decimal("8000"), current_epf=Decimal("880"))
    assert s.pcb(ytd_pcb=Decimal("1000"), ytd_zakat=Decimal("200"), **kw).employee == \
        s.pcb(ytd_pcb=Decimal("1200"), **kw).employee  # Z and X both reduce the remaining tax
    assert s.pcb(ytd_pcb=ZERO, current_zakat=Decimal("100000"), **kw).employee == 0


def test_unknown_item_blocks_and_large_deductions_warn(factory):
    _, flags = payslip(factory, emp(), items=[item("F1", "NOPE", "10")])
    assert {"unknown_pay_item"} <= {f.code for f in flags if f.severity == "error"}
    _, flags = payslip(factory, emp(), items=[item("F1", "ADVANCE", "900")])
    assert "deductions_over_half" in {f.code for f in flags}


def test_factory_overrides_and_removes_default_items(tmp_path):
    import pytest
    import yaml
    from conftest import ROOT

    from hr_agent.config import load_factory
    data = yaml.safe_load((ROOT / "config/factories/demo-pahang.yaml").read_text())
    data["pay_items"] = {"TRAVEL_ALW": None, "MEAL_ALW": {"tax": "exempt"}, "NIGHT_ALW": {"name": "Night allowance", "epf": True}}
    cfg = tmp_path / "f.yaml"
    cfg.write_text(yaml.safe_dump(data))
    f = load_factory(cfg, base_dir=ROOT)
    assert "TRAVEL_ALW" not in f.pay_items and f.pay_items["MEAL_ALW"].tax == "exempt"
    assert f.pay_items["MEAL_ALW"].epf and f.pay_items["NIGHT_ALW"].epf
    data["pay_items"] = {"PCB": {"name": "clash"}}
    cfg.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="reserved"):
        load_factory(cfg, base_dir=ROOT)
