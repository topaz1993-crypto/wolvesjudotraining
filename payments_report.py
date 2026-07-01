"""
Payments report utilities — who hasn't paid, student card, monthly summary.
"""

import os
from typing import Optional
import base64
import pickle
import warnings
from difflib import get_close_matches

warnings.filterwarnings("ignore")
import googleapiclient.discovery

SHEET_ID = "1hzkQZhmtIPL2S11Z399OmJik3pqKyOQsFp33tTNij5o"
TAB = "תשלומים"

MONTHS = ["ספטמבר", "אוקטובר", "נובמבר", "דצמבר", "ינואר",
          "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי"]

# Column indices (A=0): שם=0, שם משפחה=1, סוג מנוי=2, מועדון=3, גיל=4, ספטמבר=5...
MONTH_COL_START = 5


def _get_service():
    b64 = os.environ.get("GOOGLE_CREDS_B64")
    if b64:
        creds = pickle.loads(base64.b64decode(b64 + "=="))
    else:
        with open(os.path.expanduser("~/.wolves_judo_token.pickle"), "rb") as f:
            creds = pickle.load(f)
    return googleapiclient.discovery.build("sheets", "v4", credentials=creds)


def load_all_students() -> list[dict]:
    """Load all student rows with payment data."""
    svc = _get_service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{TAB}'!A2:U200",
    ).execute()
    rows = result.get("values", [])
    students = []
    for i, row in enumerate(rows, start=2):
        if not row or not row[0]:
            continue
        first   = row[0] if len(row) > 0 else ""
        last    = row[1] if len(row) > 1 else ""
        sub     = row[2] if len(row) > 2 else ""
        club    = row[3] if len(row) > 3 else ""
        grade   = row[4] if len(row) > 4 else ""
        payments = {}
        for j, month in enumerate(MONTHS):
            col_idx = MONTH_COL_START + j
            payments[month] = row[col_idx].strip() if len(row) > col_idx else ""
        students.append({
            "row":       i,
            "first":     first,
            "last":      last,
            "full_name": f"{first} {last}".strip(),
            "sub_type":  sub,
            "club":      club,
            "grade":     grade,
            "payments":  payments,
        })
    return students


def get_unpaid(month: str = None) -> dict[str, list]:
    """
    Returns dict {month: [student, ...]} for students with no payment.
    If month given, returns only that month.
    """
    students = load_all_students()
    months_to_check = [month] if month else MONTHS
    result = {}
    for m in months_to_check:
        unpaid = [s for s in students if s["payments"].get(m, "") == ""]
        if unpaid:
            result[m] = unpaid
    return result


def get_unpaid_by_club(month: str = None) -> dict[str, dict[str, list]]:
    """Returns {club: {month: [students]}}."""
    unpaid = get_unpaid(month)
    by_club = {}
    for m, students in unpaid.items():
        for s in students:
            club = s["club"] or "לא ידוע"
            by_club.setdefault(club, {}).setdefault(m, []).append(s)
    return by_club


def student_card(name: str) -> Optional[dict]:
    """Full student info by name (fuzzy match)."""
    students = load_all_students()
    full_names = [s["full_name"] for s in students]

    # Exact match
    for s in students:
        if name.strip() in (s["full_name"], s["first"], s["last"]):
            return s

    # Fuzzy
    matches = get_close_matches(name, full_names, n=1, cutoff=0.55)
    if matches:
        for s in students:
            if s["full_name"] == matches[0]:
                return s
    return None


def monthly_summary() -> dict:
    """Total income per month and overall stats."""
    students = load_all_students()
    summary = {}
    for month in MONTHS:
        total = 0
        paid_count = 0
        unpaid_count = 0
        for s in students:
            val = s["payments"].get(month, "")
            if val:
                try:
                    total += int(val.replace("₪", "").replace(",", "").strip())
                    paid_count += 1
                except ValueError:
                    pass
            else:
                unpaid_count += 1
        summary[month] = {
            "total":        total,
            "paid_count":   paid_count,
            "unpaid_count": unpaid_count,
        }
    return summary


def format_unpaid_message(month: str) -> str:
    """Format WhatsApp-ready message for unpaid students in a month."""
    unpaid_map = get_unpaid(month)
    if not unpaid_map or month not in unpaid_map:
        return f"✅ כל הספורטאים שילמו עבור {month}!"

    students = unpaid_map[month]
    by_club: dict[str, list] = {}
    for s in students:
        by_club.setdefault(s["club"] or "לא ידוע", []).append(s["full_name"])

    lines = [f"💰 *לא שולם — {month}* ({len(students)} ספורטאים)\n"]
    for club, names in sorted(by_club.items()):
        lines.append(f"📍 *{club}* ({len(names)}):")
        for n in sorted(names):
            lines.append(f"  • {n}")
    return "\n".join(lines)


def format_student_card(s: dict) -> str:
    """Format student info as Telegram message."""
    paid = [f"{m}: {v}₪" for m, v in s["payments"].items() if v]
    unpaid = [m for m, v in s["payments"].items() if not v]

    lines = [
        f"👤 *{s['full_name']}*",
        f"📍 מועדון: {s['club']}",
        f"🏫 כיתה: {s['grade']}",
        f"📋 מנוי: {s['sub_type']}",
        f"",
        f"✅ שילם: {', '.join(paid) if paid else 'לא שילם כלל'}",
        f"❌ חסר: {', '.join(unpaid) if unpaid else 'הכל שולם'}",
    ]
    return "\n".join(lines)


def format_monthly_report() -> str:
    """Full monthly financial summary."""
    summary = monthly_summary()
    students = load_all_students()
    total_students = len(students)

    lines = ["📊 *דו״ח חודשי — מועדון וולבס*\n"]
    grand_total = 0
    for month, data in summary.items():
        if data["paid_count"] == 0:
            continue
        lines.append(
            f"• {month}: *{data['total']:,}₪* "
            f"({data['paid_count']} שילמו, {data['unpaid_count']} חסרים)"
        )
        grand_total += data["total"]

    lines += [
        f"",
        f"💵 *סה״כ שנה: {grand_total:,}₪*",
        f"👥 סה״כ ספורטאים: {total_students}",
    ]
    return "\n".join(lines)
