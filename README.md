# Factory HR / Payroll Agent (Malaysia V1)

Deterministic payroll engine + factory-local node + multi-factory cloud AI agent.
The AI (Ilmu Claw via its OpenAI-compatible API) **never calculates pay**; it only explains figures and
calculation traces produced by the engine.

```
cloud_agent  (one instance, all factories)  --HTTPS/tunnel-->  factory_node (per factory, 24/7 server)
   LLM tools: run summary, flags, payslip                         payroll engine + Excel storage (V1)
```

## Status
MVP: attendance import, payroll calculation, approval workflow, payslip PDFs, bank file (placeholder layout).
Not production-ready until the items in *Before go-live* are done.

## Quick start
```bash
uv sync
uv run python scripts/make_sample_data.py              # fictional demo data
F=config/factories/demo-pahang.yaml
uv run hr-agent --factory $F import sample_data/zkteco_2026-09.csv --profile zkteco_csv
uv run hr-agent --factory $F run 2026-09               # creates data/demo-pahang/payroll/2026-09/run_v1.xlsx
uv run hr-agent --factory $F payslips 2026-09          # PDFs in .../payslips_v1/
uv run hr-agent --factory $F transition 2026-09 reviewed --version 1 --role accountant
uv run hr-agent --factory $F transition 2026-09 approved --version 1 --role management
uv run hr-agent --factory $F bank 2026-09
uv run pytest && uv run ruff check .
```
Payslip PDFs need Noto fonts for Chinese/Bengali/Burmese/Nepali (`fonts-noto-core fonts-noto-cjk`).

## Configuration (nothing factory-specific is hard-coded)
| File | What |
|---|---|
| `config/factories/<id>.yaml` | state, schedules, OT rates/blocks, cut-off & pay day, bank, storage, leave paid/unpaid, languages |
| `config/device_profiles/*.yaml` | column mapping for each thumbprint device export (add a file for a new brand) |
| `config/holidays/MY-<state>-<year>.yaml` | public holidays per state |
| `config/statutory/my-2026.yaml` | EPF/SOCSO/EIS/PCB parameters; official band tables go in `config/statutory/tables/*.csv` |
| `config/tenants.example.yaml` | cloud agent: factory id -> node URL + token env var + llm-gateway key env var |

## LLM usage metering
The cloud agent calls the LLM through [llm-gateway](https://github.com/littleboatforgraphic/llm-gateway), which
meters tokens per client for billing. Set `LLM_BASE_URL` to the gateway (e.g. `https://llm.example.com/v1`),
`LLM_MODEL` to a gateway model name (default `ilmu-claw`), and give each tenant its own gateway key via
`llm_key_env` (created with `gw add-key <client> hr-agent`). Each call is tagged `factory:<id>` and
`op:hr-qa` / `op:employee-qa`; employee chats send a hashed `user` id, never the employee id or name.

## Storage
V1 is Excel only (`storage.backend: excel`): `master.xlsx` (HR-maintained employees, pay items, leave,
leave balances, attendance overrides), monthly punch files, one immutable workbook per payroll run version, and `audit_log.xlsx`.
`sqlite` and `postgres` are placeholders behind the same `Store` interface.

## Pay items (allowances, incentives, benefits, PRS, deductions)
Each company defines its own pay items once; HR then enters only employee, item code and amount on the
`PayItems` sheet (`period` YYYY-MM for one-off amounts, or blank with `start`/`end` for recurring ones).
- Defaults: `config/pay_items/my-default.yaml` (allowances, incentive, bonus, benefit in kind, VOLA, employer and
  employee PRS, zakat, advance, loan, hostel, canteen). A factory file sets `pay_items_file` and can add or
  override items under `pay_items:` (`CODE: null` removes a default).
- Per item: `type` (earning / benefit_in_kind / employer_contribution / deduction), whether it counts as EPF,
  SOCSO, EIS and HRD wages, `tax` (normal / additional / exempt, with optional `tax_exempt_annual_limit`),
  `tp1_relief_annual_cap` (e.g. PRS RM3,000), `zakat` (reduces PCB), and `requires_approval`.
- Benefits in kind are added to PCB remuneration only; they are not paid and not shown on the payslip.
- Flags: unknown item code (error), unapproved item (warning, excluded), non-statutory deductions over 50% of wages.

## Payslip language and leave table
- `payslip_language_mode`: `en`, `ms`, `en+employee` or `ms+employee` (adds the employee's `language` when it is
  listed in `payslip_languages`). With two languages each label shows the main language with the second language
  on the line beneath; the leave table uses the main language only.
- Below the attendance line (working days, present, absent, OT hours) the payslip has a leave table: entitled,
  carry forward, taken this month, taken this year, balance. Annual, medical and hospitalisation always show;
  other types show when used, set on the `LeaveBalances` sheet, or configured in `leave_entitlements`.
- Entitlements: `leave_entitlements` in the factory file, never below the Employment Act minimums (annual 8/12/16 and
  medical 14/18/22 days for <2 / 2-5 / 5+ years of service, annual prorated in the first year; hospitalisation 60
  incl. MC; maternity 98 calendar days; paternity 7). `LeaveBalances` rows (employee, year, leave_type, entitled,
  carried_forward) set per-employee entitlements and days carried forward. Leave days exclude rest days and
  public holidays (maternity counts calendar days).

### Replacement leave
HR records replacement leave per employee after confirming with the employee and their manager
(`POST /leave/replacement`, saved to the `LeaveCredits` sheet and the audit log). An HR officer can also instruct the
agent to record it: `Agent.ask(..., hr_user=...)` enables the `record_replacement_leave` tool, and the factory node
accepts it only on behalf of a user with the `hr` role. Credits add to the replacement-leave entitlement for the year.

## Telegram and WhatsApp
The cloud agent web app (`uvicorn hr_agent.cloud_agent.app:app_factory --factory`) receives both channels for all
factories; each factory has its own Telegram bot and WhatsApp business number (`config/tenants.example.yaml`).
- Telegram: `POST /telegram/<factory_id>`, checked with the bot's secret token. Register it with
  `python -m hr_agent.cloud_agent.app set-telegram-webhook <factory_id> <public URL>`.
- WhatsApp Cloud API: `GET /whatsapp` (Meta verification, `WHATSAPP_VERIFY_TOKEN`) and `POST /whatsapp`
  (signature checked with `WHATSAPP_APP_SECRET`); replies go out from the factory's number.
- Who is chatting: staff (HR, accountant, management) are listed in `HR_CHAT_STAFF`
  (`config/chat_staff.example.yaml`). Employees are matched by the factory node from `telegram_id` / `phone` in
  `master.xlsx`; on Telegram an unknown employee can share their own contact once and the node links the account
  (`ChatLinks` sheet) if the number matches. Anyone else gets a polite refusal.
- Permissions: employees see only their own payslip and only once the run is locked; HR can also record
  replacement leave; accountant and management can ask about runs and flags. Replies use the employee's language.
- Phone numbers and chat ids are never sent to the LLM; usage is metered per factory with a hashed user id.

## Rules implemented
- Normal hours from schedule segments (default 08:00-12:00, 13:00-17:00 Mon-Fri; 08:00-13:00 Sat); schedules over
  9 h/day or 45 h/week are rejected.
- OT = time after scheduled end, counted in completed 30-minute blocks; 1.5x normal day.
- Rest day / public holiday: pays the higher of company policy (2x / 3x hourly) and Employment Act s.60(3) / s.60D(3).
- ORP = monthly salary / 26; hourly = ORP / 8. Unpaid leave and absence deducted by calendar days.
- Flags: missing punch, absence, OT near/over 104 h, 7 consecutive days, below minimum wage, unapproved pay item,
  permit/passport expiry, duplicate/missing bank account, unverified statutory tables/holidays.
- Statutory: EPF by citizenship/age (11%/13%|12%, 60+ 0%/4%, foreign 2%/2%); SOCSO from the official PERKESO
  table (First Category, Second Category for 60+ or `socso_category: second`, foreign workers: employer pays the
  full Second Category total, i.e. employment injury + 24-hour cover); EIS from the official Act 800 table for locals aged 18-59 (`eis_exempt` overrides);
  PCB per the LHDN 2026 computerised calculation spec (normal + additional remuneration, TP1 reliefs from pay items, zakat).
- Workflow: draft -> reviewed (accountant) -> approved (management, blocked by error flags) -> locked.

## Before go-live
1. Cross-check EPF against the KWSP Third Schedule and PCB against LHDN's MTD calculator, then set `verified: true`
   in `config/statutory/my-2026.yaml`.
2. Verify the Pahang holiday file and set `verified: true`.
3. Get Hong Leong Bank's bulk payment template (current `hlb_bulk` layout is a placeholder).
4. Run in parallel with the Excel payroll for 2 months.
5. Native-speaker review of payslip translations; add Bengali, Burmese and Nepali.

## Roadmap
Statutory submission files (EPF, SOCSO/EIS, PCB CP39), EA form / Borang E, Telegram/WhatsApp employee Q&A,
HR documents (JD, offer letters), Singapore rule pack, SQLite/PostgreSQL backends.
