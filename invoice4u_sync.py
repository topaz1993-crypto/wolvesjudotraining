"""
invoice4u_sync.py — סנכרון תשלומי invoice4u עם גיליונות Google Sheets.
כותב ל:
  • "תשלומים"                    — חגור, נווה ירק, אהרונוביץ
  • "התחשבנות ג'ודו סירקין"      — סירקין (תשלומים ישירים, לא מרוכז)
  • "חגורות"                     — תשלומי 60₪
"""

import os, pickle, base64, warnings, time
from typing import Optional
warnings.filterwarnings("ignore")
import googleapiclient.discovery

SHEET_ID = "1hzkQZhmtIPL2S11Z399OmJik3pqKyOQsFp33tTNij5o"

# ── עמודות חודשיות לפי גיליון ────────────────────────────────────────────────
# גיליון "תשלומים": עמודה F=ספטמבר ... P=יולי
MONTH_COL_PAYMENTS = {
    'ספטמבר': 'F', 'אוקטובר': 'G', 'נובמבר': 'H', 'דצמבר': 'I',
    'ינואר':  'J', 'פברואר':  'K', 'מרץ':    'L', 'אפריל':  'M',
    'מאי':    'N', 'יוני':    'O', 'יולי':   'P',
}

# גיליון "התחשבנות ג'ודו סירקין": עמודה E=ספטמבר, מחנה פסח=L (מדולג), אפריל=M
MONTH_COL_SIRKIN = {
    'ספטמבר': 'E', 'אוקטובר': 'F', 'נובמבר': 'G', 'דצמבר':  'H',
    'ינואר':  'I', 'פברואר':  'J', 'מרץ':    'K',
    # L = מחנה פסח — לא כותבים תשלום חודשי כאן
    'אפריל':  'M', 'מאי':    'N', 'יוני':   'O', 'יולי':    'P',
}

TAB_PAYMENTS = "תשלומים"
TAB_SIRKIN   = "התחשבנות ג'ודו סירקין"
TAB_BELTS    = "חגורות"


def _get_service():
    b64 = os.environ.get("GOOGLE_CREDS_B64")
    if b64:
        creds = pickle.loads(base64.b64decode(b64))
    else:
        with open(os.path.expanduser("~/.wolves_judo_token.pickle"), "rb") as f:
            creds = pickle.load(f)
    return googleapiclient.discovery.build("sheets", "v4", credentials=creds)


# ── Load students ─────────────────────────────────────────────────────────────

def load_all_students() -> list[dict]:
    """
    Returns all students from both payment tabs.
    Each dict: {first, last, branch, sheet, row_idx, sub_type}
    """
    svc = _get_service()
    students = []

    # תשלומים: A=שם, B=שם משפחה, C=סוג מנוי, D=מועדון, E=גיל
    data = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{TAB_PAYMENTS}'!A2:E200"
    ).execute().get("values", [])
    for i, row in enumerate(data, start=2):
        if len(row) < 2: continue
        first = row[0].strip()
        last  = row[1].strip()
        if not first and not last: continue
        students.append({
            'first':    first,
            'last':     last,
            'branch':   row[3].strip() if len(row) > 3 else '',
            'sub_type': row[2].strip() if len(row) > 2 else '',
            'sheet':    TAB_PAYMENTS,
            'row_idx':  i,
        })

    # התחשבנות ג'ודו סירקין: A=משפחה, B=פרטי, C=תחום, D=חוג
    data2 = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{TAB_SIRKIN}'!A2:D200"
    ).execute().get("values", [])
    for i, row in enumerate(data2, start=2):
        if len(row) < 2: continue
        last  = row[0].strip()
        first = row[1].strip()
        if not first and not last: continue
        if 'סה"כ' in last or 'תקבול' in last or 'מתאמנים' in last:
            continue
        students.append({
            'first':    first,
            'last':     last,
            'branch':   'סירקין',
            'sub_type': row[3].strip() if len(row) > 3 else '',
            'sheet':    TAB_SIRKIN,
            'row_idx':  i,
        })

    return students


# ── Read current month value ──────────────────────────────────────────────────

def get_current_value(student: dict, month: str) -> str:
    """Read what's currently written for this student+month."""
    svc = _get_service()
    tab  = student['sheet']
    col  = (MONTH_COL_SIRKIN if tab == TAB_SIRKIN else MONTH_COL_PAYMENTS).get(month)
    if not col:
        return ''
    row  = student['row_idx']
    res  = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{tab}'!{col}{row}"
    ).execute().get("values", [[]])
    return res[0][0].strip() if res and res[0] else ''


def get_prev_amount(student: dict, month: str) -> Optional[int]:
    """Return the amount from the previous paid month for this student."""
    svc = _get_service()
    tab  = student['sheet']
    cols = MONTH_COL_SIRKIN if tab == TAB_SIRKIN else MONTH_COL_PAYMENTS
    months = list(cols.keys())
    if month not in months:
        return None
    idx = months.index(month)
    # Walk backwards to find a non-empty cell
    for prev_month in reversed(months[:idx]):
        col = cols[prev_month]
        row = student['row_idx']
        res = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=f"'{tab}'!{col}{row}"
        ).execute().get("values", [[]])
        val = res[0][0].strip() if res and res[0] else ''
        if val and val.isdigit() and int(val) > 0:
            return int(val)
    return None


# ── Write payment ─────────────────────────────────────────────────────────────

def write_monthly_payment(student: dict, month: str, amount: int) -> str:
    """Write amount to the correct month cell. Returns status string."""
    svc  = _get_service()
    tab  = student['sheet']
    cols = MONTH_COL_SIRKIN if tab == TAB_SIRKIN else MONTH_COL_PAYMENTS
    col  = cols.get(month)
    if not col:
        return f"⚠️ חודש לא מוכר: {month}"
    row = student['row_idx']
    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"'{tab}'!{col}{row}",
        valueInputOption="RAW",
        body={"values": [[str(amount)]]}
    ).execute()
    name = f"{student['first']} {student['last']}"
    return f"✅ {name} — {month}: {amount}₪"


def write_monthly_batch(items: list[dict]) -> list[str]:
    """
    Write multiple payments in one batch.
    items = [{student, month, amount}, ...]
    """
    svc = _get_service()
    updates_pay = []   # for תשלומים
    updates_sir = []   # for התחשבנות סירקין

    for it in items:
        s     = it['student']
        month = it['month']
        amt   = it['amount']
        tab   = s['sheet']
        cols  = MONTH_COL_SIRKIN if tab == TAB_SIRKIN else MONTH_COL_PAYMENTS
        col   = cols.get(month)
        if not col:
            continue
        cell = f"'{tab}'!{col}{s['row_idx']}"
        upd  = {"range": cell, "values": [[str(amt)]]}
        if tab == TAB_SIRKIN:
            updates_sir.append(upd)
        else:
            updates_pay.append(upd)

    results = []
    for tab_name, updates in [(TAB_PAYMENTS, updates_pay), (TAB_SIRKIN, updates_sir)]:
        if not updates:
            continue
        try:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=SHEET_ID,
                body={"valueInputOption": "RAW", "data": updates}
            ).execute()
            results.append(f"✅ {len(updates)} עודכנו ב-{tab_name}")
        except Exception as e:
            results.append(f"❌ שגיאה ב-{tab_name}: {e}")

    return results


# ── Belt payments ─────────────────────────────────────────────────────────────

def _next_belt_row(svc) -> tuple[int, int]:
    """Return (next_empty_row_1indexed, next_row_number)."""
    data = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{TAB_BELTS}'!A:A"
    ).execute().get("values", [])
    # Skip header (row 1), find first empty row
    used_rows = len(data)
    return used_rows + 1, used_rows  # sheet row, number in col A


def write_belt_payment(first: str, last: str, branch: str,
                        date_str: str, belt_color: str = '') -> str:
    """Add one row to the חגורות sheet. Marks חשבונית=✓."""
    svc = _get_service()
    next_row, row_num = _next_belt_row(svc)
    values = [[
        str(row_num),  # A = #
        date_str,      # B = תאריך
        first,         # C = שם
        last,          # D = שם משפחה
        '',            # E = גיל (unknown from invoice)
        belt_color,    # F = צבע
        branch,        # G = מועדון
        '60',          # H = סכום
        '',            # I = מזומן/סליקה
        '✓',           # J = חשבונית (invoice4u issued it)
    ]]
    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"'{TAB_BELTS}'!A{next_row}:J{next_row}",
        valueInputOption="RAW",
        body={"values": values}
    ).execute()
    return f"🥋 {first} {last} ({branch}) — חגורה {date_str}"


# ── New joiners / dropouts ────────────────────────────────────────────────────

def check_new_joiners(attendance_names: list[str],
                      branch: str) -> list[str]:
    """
    Compare attendance list with payment sheet.
    Returns names that appear in attendance but NOT in payment sheet.
    """
    students = load_all_students()
    payment_names = {
        f"{s['first']} {s['last']}".strip()
        for s in students
        if s.get('branch') == branch or not branch
    }
    new = []
    for name in attendance_names:
        if name not in payment_names:
            new.append(name)
    return new


def format_sync_summary(written: list[dict], belts: list[str],
                         new_joiners: list[str],
                         dropouts: list[str],
                         unknowns_remaining: int) -> str:
    """Build the final summary message sent to the user."""
    lines = ["📊 *סיכום סנכרון תשלומים*\n"]
    if written:
        lines.append(f"✅ *{len(written)} ילדים עודכנו*")
        total = sum(w['amount'] for w in written)
        lines.append(f"   סה\"כ: {total:,}₪\n")
    if belts:
        lines.append(f"🥋 *{len(belts)} חגורות נוספו לגיליון*")
    if unknowns_remaining:
        lines.append(f"❓ *{unknowns_remaining} לא זוהו — נשמרו לאישורך*")
    if new_joiners:
        lines.append(f"\n📋 *מצטרפים חדשים שלא בגיליון:*")
        for n in new_joiners:
            lines.append(f"  • {n}")
    if dropouts:
        lines.append(f"\n⚠️ *פורשים שלא עודכנו:*")
        for n in dropouts:
            lines.append(f"  • {n}")
    return "\n".join(lines)
