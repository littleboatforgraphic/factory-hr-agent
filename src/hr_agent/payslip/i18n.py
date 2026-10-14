"""Fixed payslip labels. Translations must be reviewed by a native speaker before go-live.
Bengali (bn), Burmese (my) and Nepali (ne) are pending review and fall back to English."""

LABELS: dict[str, dict[str, str]] = {
    "payslip": {"en": "Payslip", "ms": "Slip Gaji", "zh": "工资单"},
    "period": {"en": "Pay period", "ms": "Tempoh gaji", "zh": "工资期"},
    "pay_date": {"en": "Pay date", "ms": "Tarikh bayaran", "zh": "发薪日期"},
    "name": {"en": "Name", "ms": "Nama", "zh": "姓名"},
    "employee_id": {"en": "Employee no.", "ms": "No. pekerja", "zh": "员工编号"},
    "id_no": {"en": "IC / Passport", "ms": "No. KP / Pasport", "zh": "身份证/护照"},
    "department": {"en": "Department", "ms": "Jabatan", "zh": "部门"},
    "position": {"en": "Position", "ms": "Jawatan", "zh": "职位"},
    "epf_no": {"en": "EPF no.", "ms": "No. KWSP", "zh": "公积金号码"},
    "socso_no": {"en": "SOCSO no.", "ms": "No. PERKESO", "zh": "社险号码"},
    "tax_no": {"en": "Tax no.", "ms": "No. cukai", "zh": "税务号码"},
    "bank": {"en": "Bank account", "ms": "Akaun bank", "zh": "银行账户"},
    "earnings": {"en": "Earnings", "ms": "Pendapatan", "zh": "收入"},
    "deductions": {"en": "Deductions", "ms": "Potongan", "zh": "扣除"},
    "gross": {"en": "Gross pay", "ms": "Gaji kasar", "zh": "总工资"},
    "total_deductions": {"en": "Total deductions", "ms": "Jumlah potongan", "zh": "扣除总额"},
    "net": {"en": "Net pay", "ms": "Gaji bersih", "zh": "实发工资"},
    "employer": {"en": "Employer contributions", "ms": "Caruman majikan", "zh": "雇主缴纳"},
    "attendance": {"en": "Attendance", "ms": "Kehadiran", "zh": "出勤"},
    "working_days": {"en": "Working days", "ms": "Hari bekerja", "zh": "工作日"},
    "present": {"en": "Present", "ms": "Hadir", "zh": "出勤"},
    "leave": {"en": "Leave", "ms": "Cuti", "zh": "休假"},
    "absent": {"en": "Absent", "ms": "Tidak hadir", "zh": "缺勤"},
    "ot_hours": {"en": "OT hours", "ms": "Jam lebih masa", "zh": "加班时数"},
    "BASIC": {"en": "Basic salary", "ms": "Gaji pokok", "zh": "基本工资"},
    "UNPAID": {"en": "Unpaid leave / absence", "ms": "Cuti tanpa gaji / tidak hadir", "zh": "无薪假/缺勤"},
    "OT_NORMAL": {"en": "Overtime 1.5x", "ms": "Lebih masa 1.5x", "zh": "加班 1.5倍"},
    "OT_REST": {"en": "Rest day work", "ms": "Kerja hari rehat", "zh": "休息日工作"},
    "OT_PH": {"en": "Public holiday work", "ms": "Kerja cuti umum", "zh": "公共假期工作"},
    "BONUS": {"en": "Bonus / incentive", "ms": "Bonus / insentif", "zh": "奖金/奖励"},
    "EPF_EE": {"en": "EPF", "ms": "KWSP", "zh": "公积金"},
    "SOCSO_EE": {"en": "SOCSO", "ms": "PERKESO", "zh": "社险"},
    "EIS_EE": {"en": "EIS", "ms": "SIP", "zh": "就业保险"},
    "PCB": {"en": "Income tax (PCB)", "ms": "Cukai pendapatan (PCB)", "zh": "所得税 (PCB)"},
    "EPF_ER": {"en": "EPF", "ms": "KWSP", "zh": "公积金"},
    "SOCSO_ER": {"en": "SOCSO", "ms": "PERKESO", "zh": "社险"},
    "EIS_ER": {"en": "EIS", "ms": "SIP", "zh": "就业保险"},
    "HRD": {"en": "HRD Corp levy", "ms": "Levi HRD Corp", "zh": "人力资源发展税"},
    "computer_generated": {
        "en": "This is a computer-generated payslip. No signature is required.",
        "ms": "Slip gaji ini dijana oleh komputer. Tandatangan tidak diperlukan.",
        "zh": "此工资单由电脑生成，无需签名。",
    },
}


def label(key: str, lang: str, fallback: str | None = None) -> str:
    """English label, plus a second line in the employee's language when available."""
    entry = LABELS.get(key)
    if not entry:
        return fallback or key
    en = entry["en"]
    other = entry.get(lang)
    return f"{en} / {other}" if other and lang != "en" else en
