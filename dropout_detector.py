"""
Dropout detector — finds students who missed 3+ consecutive training sessions.
Reads from all attendance Google Sheets.
"""

import os
import base64
import pickle
import warnings
from datetime import datetime

warnings.filterwarnings("ignore")
import googleapiclient.discovery

# Attendance sheet IDs per branch
ATTENDANCE_SHEETS = {
    "סירקין":     "1L0mcnpBPW4_3nsxaMy3EunQuOHPjWejvL1Wb6SGzltQ",
    "חגור":       "18p087VLNCRqPOhGbDzUeEg4YIHatiCfSc7v8NVFEPHA",
    "נווה ירק":   "1_J1H0q4-RGy9rH0wyhwfv-47K-uKxiHtbI-D2RoVVOU",
    "אהרונוביץ":  "1MAN8_OnQRBeiznYMvGa57GHU-xz-MErgFkkNOV_Ms8E",
    "פונקציונלי": "1LYqia2ESkLY0HD8QA0vkg1xxqLI5qx0nY9CVVj5MGGY",
}

# Cell color RGB for "present" (green) — matches the attendance.py logic
PRESENT_COLOR = (0.0, 1.0, 0.0)   # green
ABSENT_COLOR  = (1.0, 0.0, 0.0)   # red


def _get_service():
    b64 = os.environ.get("GOOGLE_CREDS_B64")
    if b64:
        creds = pickle.loads(base64.b64decode(b64 + "=="))
    else:
        with open(os.path.expanduser("~/token.pickle"), "rb") as f:
            creds = pickle.load(f)
    return googleapiclient.discovery.build("sheets", "v4", credentials=creds)


def _is_red(color: dict) -> bool:
    """Check if a cell background is red (absent)."""
    if not color:
        return False
    r = round(color.get("red", 0), 1)
    g = round(color.get("green", 0), 1)
    b = round(color.get("blue", 0), 1)
    return r >= 0.9 and g <= 0.1 and b <= 0.1


def _is_green(color: dict) -> bool:
    """Check if a cell background is green (present)."""
    if not color:
        return False
    r = round(color.get("red", 0), 1)
    g = round(color.get("green", 0), 1)
    b = round(color.get("blue", 0), 1)
    return g >= 0.9 and r <= 0.1 and b <= 0.1


def _get_sheet_tabs(svc, sheet_id: str) -> list[str]:
    meta = svc.spreadsheets().get(spreadsheetId=sheet_id).execute()
    return [s["properties"]["title"] for s in meta["sheets"]]


def find_at_risk_students(consecutive: int = 3) -> list[dict]:
    """
    Scan all attendance sheets.
    Returns list of {name, branch, group, missed_count} for students
    who missed 'consecutive' or more training sessions in a row (most recent).
    """
    svc = _get_service()
    at_risk = []

    for branch, sheet_id in ATTENDANCE_SHEETS.items():
        try:
            tabs = _get_sheet_tabs(svc, sheet_id)
        except Exception:
            continue

        for tab in tabs:
            try:
                # Get cell values
                val_result = svc.spreadsheets().values().get(
                    spreadsheetId=sheet_id,
                    range=f"'{tab}'!A1:ZZ200",
                ).execute()
                rows = val_result.get("values", [])
                if not rows:
                    continue

                # Get formatting (colors)
                fmt_result = svc.spreadsheets().get(
                    spreadsheetId=sheet_id,
                    ranges=[f"'{tab}'!A1:ZZ200"],
                    includeGridData=True,
                ).execute()

                grid_data = fmt_result["sheets"][0].get("data", [{}])[0]
                grid_rows = grid_data.get("rowData", [])

                # Row 0 = header (dates), rows 1+ = students
                if not rows or len(rows) < 2:
                    continue

                # Find date columns (columns with dates in header row)
                header = rows[0]
                date_cols = []
                for ci, cell in enumerate(header):
                    if ci == 0:
                        continue  # skip name column
                    val = str(cell).strip()
                    if val and any(c.isdigit() for c in val):
                        date_cols.append(ci)

                if not date_cols:
                    continue

                # Take last N date columns (most recent sessions)
                recent_cols = date_cols[-consecutive:]

                for ri in range(1, len(rows)):
                    row_vals = rows[ri] if ri < len(rows) else []
                    name = row_vals[0].strip() if row_vals else ""
                    if not name:
                        continue

                    # Get colors for this student's recent sessions
                    missed = 0
                    for ci in recent_cols:
                        cell_color = {}
                        try:
                            cell_data = grid_rows[ri]["values"][ci]
                            cell_color = (
                                cell_data.get("effectiveFormat", {})
                                         .get("backgroundColor", {})
                            )
                        except (IndexError, KeyError):
                            pass

                        if _is_red(cell_color):
                            missed += 1
                        elif _is_green(cell_color):
                            break  # was present — no consecutive miss

                    if missed >= consecutive:
                        at_risk.append({
                            "name":        name,
                            "branch":      branch,
                            "group":       tab,
                            "missed":      missed,
                        })

            except Exception:
                continue

    return at_risk


def format_at_risk_message(at_risk: list[dict], consecutive: int = 3) -> str:
    """Format dropout alert as Telegram message."""
    if not at_risk:
        return f"✅ אין ספורטאים שפספסו {consecutive}+ אימונים ברצף."

    by_branch: dict[str, list] = {}
    for s in at_risk:
        by_branch.setdefault(s["branch"], []).append(s)

    lines = [f"⚠️ *התראת נשירה — {consecutive}+ אימונים ברצף*\n"]
    for branch, students in sorted(by_branch.items()):
        lines.append(f"📍 *{branch}* ({len(students)}):")
        for s in students:
            lines.append(f"  • {s['name']} | {s['group']} | פספס {s['missed']} אימונים")
    lines.append(f"\nסה״כ בסיכון: {len(at_risk)} ספורטאים")
    return "\n".join(lines)
