"""Command-line entry point: hr-agent <command> --factory config/factories/<id>.yaml"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from hr_agent import service
from hr_agent.bank import write_bank_file
from hr_agent.config import load_factory
from hr_agent.payslip import render_payslip_pdf
from hr_agent.storage import open_store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hr-agent")
    ap.add_argument("--factory", required=True, help="Path to factory YAML config")
    ap.add_argument("--user", default=os.environ.get("USER", "cli"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="Create the master Excel template")
    imp = sub.add_parser("import", help="Import a thumbprint device export")
    imp.add_argument("file")
    imp.add_argument("--profile", required=True)
    run = sub.add_parser("run", help="Calculate payroll for a period (YYYY-MM)")
    run.add_argument("period")
    pay = sub.add_parser("payslips", help="Render payslip PDFs for a run")
    pay.add_argument("period")
    pay.add_argument("--version", type=int)
    bk = sub.add_parser("bank", help="Write the bank payment file for an approved/locked run")
    bk.add_argument("period")
    bk.add_argument("--version", type=int)
    tr = sub.add_parser("transition", help="Review / approve / lock a run")
    tr.add_argument("period")
    tr.add_argument("status", choices=["reviewed", "approved", "locked"])
    tr.add_argument("--version", type=int, required=True)
    tr.add_argument("--role", required=True, choices=["hr", "accountant", "management"])
    tr.add_argument("--note", default="")
    args = ap.parse_args(argv)

    factory = load_factory(args.factory)
    store = open_store(factory)

    if args.cmd == "init":
        print(store.init_master())
    elif args.cmd == "import":
        print(json.dumps(service.import_attendance(factory, store, args.file, args.profile, args.user), indent=2))
    elif args.cmd == "run":
        r = service.run_payroll(factory, store, args.period, args.user)
        print(json.dumps(service.run_summary(r), indent=2))
        for f in r.flags:
            print(f"  [{f.severity}] {f.code} {f.employee_id or ''} {f.date or ''} {f.message}")
    elif args.cmd == "payslips":
        r = store.load_run(args.period, args.version)
        if r is None:
            raise SystemExit(f"No payroll run for {args.period}")
        emps = {e.employee_id: e for e in store.employees()}
        out_dir = Path(store.root) / "payroll" / r.period / f"payslips_v{r.version}"
        for p in r.payslips:
            print(render_payslip_pdf(p, emps[p.employee_id], factory, out_dir / f"{p.employee_id}.pdf"))
    elif args.cmd == "bank":
        r = store.load_run(args.period, args.version)
        if r is None:
            raise SystemExit(f"No payroll run for {args.period}")
        emps = {e.employee_id: e for e in store.employees()}
        print(write_bank_file(r, emps, factory, Path(store.root) / "payroll" / r.period))
    elif args.cmd == "transition":
        r = service.transition(store, args.period, args.version, args.status, args.user, args.role, args.note)
        print(f"{r.period} v{r.version} -> {r.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
