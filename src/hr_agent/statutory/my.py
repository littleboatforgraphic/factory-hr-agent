"""Malaysian statutory contributions: EPF, SOCSO, EIS, PCB (MTD).

Band-table approximations are used unless an official CSV table is supplied.
Every result carries a trace string so the AI agent and HR can explain it.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from hr_agent.money import ZERO, D, ceil_5sen, ceil_ringgit, ceil_to_step, round_cents

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
            rows = [
                (D(r["wage_from"]), D(r["wage_to"]), D(r["employee"]), D(r["employer"]))
                for r in csv.DictReader(fh)
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
            ("socso", "local"): table("table_file", "socso"),
            ("socso", "foreign"): table("foreign_table_file", "socso"),
            ("eis", "local"): table("table_file", "eis"),
        }

    def uses_official_tables(self) -> dict[str, bool]:
        return {f"{k[0]}_{k[1]}": v is not None for k, v in self.tables.items()}

    # EPF --------------------------------------------------------------
    def epf(self, wage: Decimal, category: str) -> Contribution:
        wage = round_cents(wage)
        if wage <= 0:
            return Contribution(ZERO, ZERO, "No EPF wages")
        official = self.tables[("epf", category)]
        if official and (hit := official.lookup(wage)):
            return Contribution(hit[0], hit[1], f"EPF official table ({category}) for wage RM{wage}")
        c = self.cfg["epf"][category]
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
            f"approx. employee RM{ee}, employer RM{er} - replace with official table",
        )

    def socso(self, wage: Decimal, category: str) -> Contribution:
        if wage <= 0:
            return Contribution(ZERO, ZERO, "No SOCSO wages")
        official = self.tables[("socso", category)]
        if official and (hit := official.lookup(round_cents(wage))):
            return Contribution(hit[0], hit[1], f"SOCSO official table ({category})")
        c = self.cfg["socso"]
        rates = c[category]
        label = "SOCSO Cat.1" if category == "local" else "SOCSO Employment Injury (foreign)"
        return self._banded(wage, D(c["wage_ceiling"]), D(rates["employee_rate"]), D(rates["employer_rate"]), label)

    def eis(self, wage: Decimal, category: str) -> Contribution:
        if category != "local" or wage <= 0:
            return Contribution(ZERO, ZERO, "EIS not applicable" if category != "local" else "No EIS wages")
        official = self.tables[("eis", "local")]
        if official and (hit := official.lookup(round_cents(wage))):
            return Contribution(hit[0], hit[1], "EIS official table")
        c = self.cfg["eis"]
        return self._banded(wage, D(c["wage_ceiling"]), D(c["employee_rate"]), D(c["employer_rate"]), "EIS")

    # PCB ----------------------------------------------------------------
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
        p = self.cfg["pcb"]
        tax = self._tax_on(max(chargeable, ZERO))
        if chargeable <= D(p["rebate_threshold"]):
            rebate = D(p["individual_rebate"]) + (D(p["spouse_rebate"]) if married_nonworking_spouse else ZERO)
            tax -= rebate
        return max(tax, ZERO)

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
    ) -> Contribution:
        p = self.cfg["pcb"]
        if not resident:
            amount = round_cents((current_taxable + additional_taxable) * D(p["non_resident_rate"]))
            return Contribution(amount, ZERO, f"PCB non-resident flat {D(p['non_resident_rate']):.0%}")
        n = 12 - month
        cap = D(p["epf_relief_cap"])
        k = min(ytd_epf, cap)
        k1 = min(current_epf, max(cap - k, ZERO))
        k2 = min(k1, max(cap - k - k1, ZERO) / n) if n else ZERO
        reliefs = D(p["individual_relief"]) + D(children) * D(p["child_relief"])
        if married_nonworking_spouse:
            reliefs += D(p["spouse_relief"])
        y_ytd_net = ytd_taxable - k
        p_normal = y_ytd_net + (current_taxable - k1) + (current_taxable - k2) * n - reliefs
        annual = self._annual_tax(p_normal, married_nonworking_spouse)
        normal_pcb = max((annual - ytd_pcb) / (n + 1), ZERO)
        trace = (
            f"PCB: P = RM{round_cents(p_normal)} (YTD net RM{round_cents(y_ytd_net)}, "
            f"{n} months remaining, reliefs RM{reliefs}); annual tax RM{round_cents(annual)}; "
            f"less YTD PCB RM{round_cents(ytd_pcb)} / {n + 1}"
        )
        ar_pcb = ZERO
        if additional_taxable > 0:
            kt = min(additional_epf, max(cap - k - k1 - k2 * n, ZERO))
            p_ar = p_normal + additional_taxable - kt
            annual_ar = self._annual_tax(p_ar, married_nonworking_spouse)
            ar_pcb = max(annual_ar - (ytd_pcb + normal_pcb * (n + 1)), ZERO)
            trace += f"; additional remuneration RM{round_cents(additional_taxable)} adds RM{round_cents(ar_pcb)}"
        total = ceil_5sen(normal_pcb + ar_pcb)
        if total < D(p["minimum_deduction"]):
            return Contribution(ZERO, ZERO, trace + f"; total RM{total} below RM{p['minimum_deduction']} -> not deducted")
        return Contribution(total, ZERO, trace + f"; PCB = RM{total} (rounded up to 5 sen)")
