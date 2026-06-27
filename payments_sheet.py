"""
Payments sheet manager — read and update the תשלומים tab.
"""

import os
import base64
import pickle
import warnings
from difflib import get_close_matches

warnings.filterwarnings("ignore")
import googleapiclient.discovery

SHEET_ID = "1hzkQZhmtIPL2S11Z399OmJik3pqKyOQsFp33tTNij5o"
TAB      = "תשלומים"

MONTH_COLS = {
    "ספטמבר":  "F",
    "אוקטובר": "G",
    "נובמבר":  "H",
    "דצמבר":   "I",
    "ינואר":   "J",
    "פברואר":  "K",
    "מרץ":     "L",
    "אפריל":   "M",
    "מאי":     "N",
    "יוני":    "O",
    "יולי":    "P",
}

# Column index (1-based) matching the above letters
MONTH_COL_INDEX = {
    "ספטמבר":  6,
    "אוקטובר": 7,
    "נובמבר":  8,
    "דצמבר":   9,
    "ינואר":   10,
    "פברואר":  11,
    "מרץ":     12,
    "אפריל":   13,
    "מאי":     14,
    "יוני":    15,
    "יולי":    16,
}


def _get_service():
    b64 = os.environ.get("GOOGLE_CREDS_B64")
    if b64:
        creds = pickle.loads(base64.b64decode(b64))
    else:
        with open(os.path.expanduser("~/.wolves_judo_token.pickle"), "rb") as f:
            creds = pickle.load(f)
    return googleapiclient.discovery.build("sheets", "v4", credentials=creds)


def load_students() -> list[dict]:
    """Load all students from sheet. Returns list of {row, first, last, club, full_name}."""
    svc = _get_service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{TAB}'!A2:E200",
    ).execute()
    rows = result.get("values", [])
    students = []
    for i, row in enumerate(rows, start=2):
        if not row:
            continue
        first = row[0] if len(row) > 0 else ""
        last  = row[1] if len(row) > 1 else ""
        club  = row[3] if len(row) > 3 else ""
        if first or last:
            students.append({
                "row":       i,
                "first":     first,
                "last":      last,
                "club":      club,
                "full_name": f"{first} {last}".strip(),
            })
    return students


def find_student(name: str) -> dict | None:
    """Fuzzy-find student by name. Returns best match or None."""
    students = load_students()
    full_names = [s["full_name"] for s in students]

    # Exact match first
    for s in students:
        if name.strip() == s["full_name"] or name.strip() == s["first"] or name.strip() == s["last"]:
            return s

    # Fuzzy match
    matches = get_close_matches(name, full_names, n=1, cutoff=0.6)
    if matches:
        for s in students:
            if s["full_name"] == matches[0]:
                return s
    return None


def get_month_value(student_row: int, month: str) -> str:
    """Get current value in a month column for a student."""
    col = MONTH_COLS.get(month)
    if not col:
        return ""
    svc = _get_service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{TAB}'!{col}{student_row}",
    ).execute()
    vals = result.get("values", [[]])
    return vals[0][0] if vals and vals[0] else ""


def update_payment(student_row: int, month: str, amount: str) -> bool:
    """Write payment amount to the correct cell. Returns True on success."""
    col = MONTH_COLS.get(month)
    if not col:
        return False
    svc = _get_service()
    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"'{TAB}'!{col}{student_row}",
        valueInputOption="USER_ENTERED",
        body={"values": [[amount]]},
    ).execute()
    return True


def payment_summary_row(student: dict) -> str:
    """Return a summary of what's already paid for this student."""
    svc = _get_service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{TAB}'!F{student['row']}:P{student['row']}",
    ).execute()
    vals = result.get("values", [[]])[0] if result.get("values") else []
    months = list(MONTH_COLS.keys())
    paid = []
    for i, v in enumerate(vals):
        if v and i < len(months):
            paid.append(f"{months[i]}: {v}₪")
    return ", ".join(paid) if paid else "אין תשלומים רשומים"
