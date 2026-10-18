"""Malaysian statutory contributions: EPF, SOCSO, EIS, PCB (MTD).

SOCSO/EIS use the official PERKESO band tables in config/statutory/tables/*.csv; band-table
approximations are only a fallback when a table is missing. PCB follows the LHDN 2026
computerised calculation specification. Every result carries a trace string so the AI agent
and HR can explain it.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from hr_agent.money import ZERO, D, ceil_5sen, ceil_ringgit, ceil_to_step, round_cents, trunc_cents

# PERKESO wage bands below RM200 are irregular; above that they are RM100 wide.
_SOCSO_LOW_BANDS = [30, 50, 70, 100, 140, 200]


@dataclass
class Contribution:
    employee: Decimal
    employer: Decimal
    trace: str


@dataclass
class BandTable:
    rows: list[tuple[Decimal, Decimal, Decimal, Decimal]]

    @classmethod
    def load(cls, path: Path) -> BandTable | None:
        if not path.exists():
            return None
        with path.open(encoding="utf-8-sig", newline="") as fh:
            lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
        rows = [
            (D(r["wage_from"]), D(r["wage_to"]), D(r["employee"]), D(r["employer"]))
            for r in csv.DictReader(lines)
        ]
        return cls(rows=rows)

    def lookup(self, wage: Decimal) -> tuple[Decimal, Decimal] | None:
        for lo, hi, ee, er in self.rows:
            if lo <= wage <= hi:
                return ee, er
        return None


class MalaysiaStatutory:
    def __init__(self, rates_file: Path, base_dir: Path):
        self.cfg = yaml.safe_load(Path(rates_file).read_text(encoding="utf-8"))
        self.verified: bool = bool(self.cfg.get("verified"))

        def table(key: str, section: str) -> BandTable | None:
            rel = self.cfg[section].get(key)
            return BandTable.load(base_dir / rel) if rel else None

        self.tables = {
            ("epf", "local"): table("table_file", "epf"),
            ("epf", "foreign"): table("foreign_table_file", "epf"),
            ("epf", "local_60_plus"): table("local_60_plus_table_file", "epf"),
            ("socso", "first"): table("first_category_table_file", "socso"),
            ("socso", "second"): table("second_category_table_file", "socso"),
            ("socso", "foreign"): table("foreign_table_file", "socso"),
            ("eis", "local"): table("table_file", "eis"),
        }

    def uses_official_tables(self) -> dict[str, bool]:
        return {f"{k[0]}_{k[1]}": v is not None for k, v in self.tables.items()}

    # EPF --------------------------------------------------------------
    def epf_rate_key(self, category: str, age: int | None) -> str:
        if category == "local" and age is not None and age >= 60 and "local_60_plus" in self.cfg["epf"]:
            return "local_60_plus"
        return category

    def epf(self, wage: Decimal, category: str, age: int | None = None) -> Contribution:
        wage = round_cents(wage)
        if wage <= 0:
            return Contribution(ZERO, ZERO, "No EPF wages")
        key = self.epf_rate_key(category, age)
        official = self.tables.get(("epf", key))
        if official and (hit := official.lookup(wage)):
            return Contribution(hit[0], hit[1], f"EPF official table ({key}) for wage RM{wage}")
        c = self.cfg["epf"][key]
        category = key
        if wage <= 10:
            return Contribution(ZERO, ZERO, "EPF: wages RM10 and below are exempt")
        if wage <= 5000:
            basis = ceil_to_step(wage, 20)
        elif wage <= 20000:
            basis = ceil_to_step(wage, 100)
        else:
            basis = wage
        er_rate = D(c["employer_rate_low"] if wage <= D(c["employer_rate_threshold"]) else c["employer_rate_high"])
        ee_rate = D(c["employee_rate"])
        ee, er = ceil_ringgit(basis * ee_rate), ceil_ringgit(basis * er_rate)
        return Contribution(
            ee,
            er,
            f"EPF ({category}): wage RM{wage} -> band ceiling RM{basis}; "
            f"employee {ee_rate:.0%} = RM{ee}, employer {er_rate:.0%} = RM{er} (rounded up to RM1)",
        )

    # SOCSO / EIS --------------------------------------------------------
    @staticmethod
    def _band(wage: Decimal, ceiling: Decimal) -> tuple[Decimal, Decimal]:
        wage = min(wage, ceiling)
        lower = ZERO
        for upper in _SOCSO_LOW_BANDS:
            if wage <= upper:
                return lower, D(upper)
            lower = D(upper)
        upper = min(ceil_to_step(wage, 100), ceiling)
        return upper - 100, upper

    def _banded(self, wage: Decimal, ceiling: Decimal, ee_rate: Decimal, er_rate: Decimal, label: str) -> Contribution:
        lo, hi = self._band(wage, ceiling)
        mid = (lo + hi) / 2
        ee, er = ceil_5sen(mid * ee_rate), ceil_5sen(mid * er_rate)
        return Contribution(
            ee,
            er,
            f"{label}: wage RM{round_cents(wage)} in band RM{lo}-{hi} (ceiling RM{ceiling}); "
            f"approx. employee RM{ee}, employer RM{er} - official table missing",
        )

    @staticmethod
    def socso_category(category: str, age: int | None, override: str = "auto") -> str:
        """first = EI + invalidity + non-employment injury; second = 60+ or first joined at 55+."""
        if category != "local":
            return "foreign"
        if override in ("first", "second"):
            return override
        return "second" if age is not None and age >= 60 else "first"

    def socso(self, wage: Decimal, category: str, age: int | None = None, override: str = "auto") -> Contribution:
        if wage <= 0:
            return Contribution(ZERO, ZERO, "No SOCSO wages")
        key = self.socso_category(category, age, override)
        label = {"first": "SOCSO First Category", "second": "SOCSO Second Category",
                 "foreign": "SOCSO Employment Injury (foreign worker, employer only)"}[key]
        wage = round_cents(wage)
        official = self.tables[("socso", key)]
        if official and (hit := official.lookup(wage)):
            return Contribution(hit[0], hit[1], f"{label}, PERKESO table for wage RM{wage}: employee RM{hit[0]}, employer RM{hit[1]}")
        c = self.cfg["socso"]
        rates = c[key]
        return self._banded(wage, D(c["wage_ceiling"]), D(rates["employee_rate"]), D(rates["employer_rate"]), label)

    def eis(self, wage: Decimal, category: str, age: int | None = None, exempt: bool = False) -> Contribution:
        if category != "local":
            return Contribution(ZERO, ZERO, "EIS not applicable to foreign workers")
        c = self.cfg["eis"]
        if exempt:
            return Contribution(ZERO, ZERO, "EIS exempt (employee record)")
        if age is not None and not (c["min_age"] <= age < c["max_age"]):
            return Contribution(ZERO, ZERO, f"EIS not applicable at age {age} (covers {c['min_age']}-{c['max_age'] - 1})")
        if wage <= 0:
            return Contribution(ZERO, ZERO, "No EIS wages")
        wage = round_cents(wage)
        official = self.tables[("eis", "local")]
        if official and (hit := official.lookup(wage)):
            return Contribution(hit[0], hit[1], f"EIS PERKESO table for wage RM{wage}: employee RM{hit[0]}, employer RM{hit[1]}")
        return self._banded(wage, D(c["wage_ceiling"]), D(c["employee_rate"]), D(c["employer_rate"]), "EIS")

    # PCB (LHDN computerised calculation, 2026 specification) -------------
    def _tax_on(self, chargeable: Decimal) -> Decimal:
        brackets = [(D(lo), D(rate)) for lo, rate in self.cfg["pcb"]["brackets"]]
        tax = ZERO
        for i, (lo, rate) in enumerate(brackets):
            hi = brackets[i + 1][0] if i + 1 < len(brackets) else None
            if chargeable <= lo:
                break
            top = chargeable if hi is None else min(chargeable, hi)
            tax += (top - lo) * rate
        return tax

    def _annual_tax(self, chargeable: Decimal, married_nonworking_spouse: bool) -> Decimal:
        """(P - M) x R + B from Table 1; B already nets the individual/spouse rebate when P <= RM35,000."""
        p = self.cfg["pcb"]
        tax = self._tax_on(max(chargeable, ZERO))
        if chargeable <= D(p["rebate_threshold"]):
            tax -= D(p["individual_rebate"]) + (D(p["spouse_rebate"]) if married_nonworking_spouse else ZERO)
        return trunc_cents(max(tax, ZERO))

    def pcb(
        self,
        *,
        month: int,
        resident: bool,
        married_nonworking_spouse: bool,
        children: int,
        ytd_taxable: Decimal,
        ytd_epf: Decimal,
        ytd_pcb: Decimal,
        current_taxable: Decimal,
        current_epf: Decimal,
        additional_taxable: Decimal = ZERO,
        additional_epf: Decimal = ZERO,
        ytd_deductions: Decimal = ZERO,
        current_deductions: Decimal = ZERO,
    ) -> Contribution:
        """MTD = [(P - M) R + B - (Z + X)] / (n + 1); figures truncated to 2 decimals, MTD rounded up to 5 sen.

        ytd_deductions / current_deductions are the TP1 allowable deductions (sum LP, LP1).
        """
        p = self.cfg["pcb"]
        minimum = D(p["minimum_deduction"])
        if not resident:
            amount = round_cents((current_taxable + additional_taxable) * D(p["non_resident_rate"]))
            return Contribution(amount, ZERO, f"PCB non-resident flat {D(p['non_resident_rate']):.0%}")
        n = 12 - month
        cap = D(p["epf_relief_cap"])
        k = min(ytd_epf, cap)
        k1 = min(current_epf, cap - k)

        def k2(kt: Decimal) -> Decimal:
            return trunc_cents(min(k1, max(cap - k - k1 - kt, ZERO) / n)) if n else ZERO

        reliefs = D(p["individual_relief"]) + D(children) * D(p["child_relief"]) + ytd_deductions + current_deductions
        if married_nonworking_spouse:
            reliefs += D(p["spouse_relief"])
        net_to_date = (ytd_taxable - k) + (current_taxable - k1)

        k2_normal = k2(ZERO)
        p_normal = trunc_cents(net_to_date + (current_taxable - k2_normal) * n - reliefs)
        annual = self._annual_tax(p_normal, married_nonworking_spouse)
        normal = ceil_5sen(max(trunc_cents((annual - ytd_pcb) / (n + 1)), ZERO))
        trace = (
            f"PCB: K2 RM{k2_normal}, P = RM{p_normal} ({n} months remaining, reliefs RM{reliefs}); "
            f"annual tax RM{annual}; less MTD paid RM{round_cents(ytd_pcb)}, / {n + 1} = RM{normal}"
        )
        if normal < minimum:
            trace += f" (below RM{minimum}: not deducted)"
            normal_paid = ZERO
        else:
            normal_paid = normal

        ar = ZERO
        if additional_taxable > 0:
            kt = min(additional_epf, max(cap - k - k1, ZERO))
            k2_ar = k2(kt)
            p_ar = trunc_cents(net_to_date + (current_taxable - k2_ar) * n + (additional_taxable - kt) - reliefs)
            annual_ar = self._annual_tax(p_ar, married_nonworking_spouse)
            year_mtd = ytd_pcb + normal * (n + 1)
            ar = ceil_5sen(max(annual_ar - year_mtd, ZERO))
            trace += (
                f"; additional remuneration RM{round_cents(additional_taxable)} (Kt RM{kt}, K2 RM{k2_ar}): "
                f"P = RM{p_ar}, annual tax RM{annual_ar} less year MTD RM{year_mtd} = RM{ar}"
            )
            if ar < minimum:
                trace += f" (below RM{minimum}: not deducted)"
                ar = ZERO
        total = normal_paid + ar
        return Contribution(total, ZERO, trace + f"; PCB = RM{total}")
