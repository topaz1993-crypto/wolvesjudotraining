"""
Attendance module for Wolves Judo Telegram bot.
Manages Google Sheets attendance marking with color coding.
"""

import os
import base64
import pickle
import json
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")
import googleapiclient.discovery

DROPOUT_UNDO_FILE = Path("dropout_undo.json")

HEBREW_MONTHS = {
    1: "ינואר", 2: "פברואר", 3: "מרץ", 4: "אפריל",
    5: "מאי", 6: "יוני", 7: "יולי", 8: "אוגוסט",
    9: "ספטמבר", 10: "אוקטובר", 11: "נובמבר", 12: "דצמבר",
}

# Spreadsheet IDs per branch
BRANCH_SHEETS = {
    "סירקין":      "1L0mcnpBPW4_3nsxaMy3EunQuOHPjWejvL1Wb6SGzltQ",
    "נווה ירק":    "1_J1H0q4-RGy9rH0wyhwfv-47K-uKxiHtbI-D2RoVVOU",
    "פונקציונלי":  "1LYqia2ESkLY0HD8QA0vkg1xxqLI5qx0nY9CVVj5MGGY",
    "אהרונוביץ":   "1MAN8_OnQRBeiznYMvGa57GHU-xz-MErgFkkNOV_Ms8E",
    "חגור":        "18p087VLNCRqPOhGbDzUeEg4YIHatiCfSc7v8NVFEPHA",
}

PAYMENTS_SHEET_ID = "1hzkQZhmtIPL2S11Z399OmJik3pqKyOQsFp33tTNij5o"

# Tab names per branch (for fuzzy matching)
BRANCH_GROUPS = {
    "סירקין":     ["ד-ו", "ג", "א-ב", "גנים", "ז-בוגרים", "נבחרת צעירה", "נבחרת בוגרת", "איפון פייט ב-ד", "איפון פייט ה-ז"],
    "נווה ירק":   ["גנים", "ג-ו", "א-ב"],
    "פונקציונלי": ["ז-ח", 'ט-י"ב'],
    "אהרונוביץ":  ["א-ה"],
    "חגור":       ["ד-ח", "א-ג", "גנים"],
}

GREEN = {"red": 0.0, "green": 1.0, "blue": 0.0}
RED   = {"red": 1.0, "green": 0.0, "blue": 0.0}
BLACK = {"red": 0.0, "green": 0.0, "blue": 0.0}
ORANGE_BG = {"red": 0.90, "green": 0.45, "blue": 0.10}  # month header

# Design palette
_NAVY       = {"red": 0.13, "green": 0.19, "blue": 0.36}
_WHITE      = {"red": 1.0,  "green": 1.0,  "blue": 1.0}
_DATE_BG    = {"red": 0.82, "green": 0.85, "blue": 0.89}
_ROW_A      = {"red": 0.93, "green": 0.95, "blue": 0.99}  # odd student rows
_ROW_B      = {"red": 0.97, "green": 0.98, "blue": 1.00}  # even student rows
_BORDER     = {"red": 0.70, "green": 0.72, "blue": 0.76}
_BORDER_OUTER = _NAVY

# לוח אימונים קבוע: יום בשבוע (0=שני ... 6=ראשון) → [(סניף, קבוצה, שעה)]
# Python weekday: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
# ישראל: א׳=ראשון(6), ב׳=שני(0), ג׳=שלישי(1), ד׳=רביעי(2), ה׳=חמישי(3), ו׳=שישי(4)
WEEKLY_SCHEDULE = {
    6: [  # א׳ — ראשון (חגור בלבד)
        ("חגור", "ד-ח",  "15:15"),
        ("חגור", "א-ג",  "16:30"),
        ("חגור", "גנים", "17:15"),
    ],
    0: [  # ב׳ — שני (סירקין — ללא גנים)
        ("סירקין", "ד-ו",       "14:30"),
        ("סירקין", "ג",          "15:30"),
        ("סירקין", "א-ב",        "16:30"),
        ("סירקין", "ז-בוגרים",   "18:00"),
    ],
    1: [  # ג׳ — שלישי (נווה ירק בלבד)
        ("נווה ירק", "גנים", "16:00"),
        ("נווה ירק", "ג-ו",  "16:45"),
        ("נווה ירק", "א-ב",  "17:45"),
    ],
    2: [  # ד׳ — רביעי (אהרונוביץ, פונקציונלי, איפון פייט)
        ("אהרונוביץ",  "א-ה",              "13:50"),
        ("פונקציונלי", "ז-ח",              "16:15"),
        ("פונקציונלי", 'ט-י"ב',            "17:15"),
        ("סירקין",     "איפון פייט ב-ד",   "18:30"),
        ("סירקין",     "איפון פייט ה-ז",   "19:15"),
    ],
    3: [  # ה׳ — חמישי (סירקין מלא)
        ("סירקין", "ד-ו",        "14:30"),
        ("סירקין", "ג",           "15:30"),
        ("סירקין", "א-ב",         "16:30"),
        ("סירקין", "גן חובה",     "17:15"),
        ("סירקין", "ז-בוגרים",    "18:00"),
    ],
    4: [  # ו׳ — שישי (פונקציונלי + נבחרות)
        ("פונקציונלי", "ז-ח",          "09:00"),
        ("פונקציונלי", 'ט-י"ב',        "10:00"),
        ("סירקין",     "נבחרת צעירה",  "13:15"),
        ("סירקין",     "נבחרת בוגרת",  "15:30"),
    ],
}


def get_todays_schedule() -> list[tuple[str, str, str]]:
    """Return list of (branch, group, time) for today."""
    day = datetime.now().weekday()
    return WEEKLY_SCHEDULE.get(day, [])


def _get_service():
    # Support both local pickle and base64 env var (for Render)
    b64 = os.environ.get("GOOGLE_CREDS_B64")
    if b64:
        creds = pickle.loads(base64.b64decode(b64))
    else:
        pickle_path = os.path.expanduser("~/.wolves_judo_token.pickle")
        with open(pickle_path, "rb") as f:
            creds = pickle.load(f)
    return googleapiclient.discovery.build("sheets", "v4", credentials=creds)


def resolve_branch_group(text: str):
    """Return (branch_name, group_name) or (None, None) if not found."""
    text_lower = text.lower()
    found_branch = None
    for branch in BRANCH_SHEETS:
        if branch in text:
            found_branch = branch
            break
    if not found_branch:
        return None, None

    found_group = None
    for group in BRANCH_GROUPS[found_branch]:
        if group in text:
            found_group = group
            break
    return found_branch, found_group


def _detect_student_start_row(service, spreadsheet_id: str, sheet_name: str) -> int:
    """Return the 1-based row where students start (first row with a digit in col A, min row 3)."""
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1:A6"
    ).execute()
    rows = result.get("values", [])
    for i, row in enumerate(rows, start=1):
        if i < 3:
            continue
        val = row[0].strip() if row else ""
        if val.isdigit():
            return i
    return 4  # default


def _detect_structure(service, spreadsheet_id: str, sheet_name: str) -> dict:
    """
    Auto-detect sheet structure: header row, student start row, first attendance column.
    Returns dict with keys: header_row_0, student_start_0, first_att_col_0 (all 0-based).
    """
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!1:5"
    ).execute()
    rows = result.get("values", [])

    # Find header row: row containing "שם" in col B (index 1)
    header_row_0 = 1  # default
    for i, row in enumerate(rows):
        if len(row) > 1 and row[1].strip() == "שם":
            header_row_0 = i
            break

    # Find student start: first row (>= row 3, 0-based >= 2) with digit in col A
    student_start_0 = 3  # default
    for i, row in enumerate(rows):
        if i < 2:
            continue
        val = row[0].strip() if row else ""
        if val.isdigit():
            student_start_0 = i
            break

    # Find first attendance column: first col >= 3 with a digit in the header row or row below
    first_att_col_0 = 5  # default col F
    for check_0 in [header_row_0, header_row_0 - 1, header_row_0 + 1]:
        if check_0 < 0 or check_0 >= len(rows):
            continue
        row = rows[check_0]
        for j, cell in enumerate(row):
            if j >= 3 and cell.strip().isdigit():
                first_att_col_0 = j
                break
        if first_att_col_0 != 5 or (rows[check_0][5].strip().isdigit() if check_0 < len(rows) and len(rows[check_0]) > 5 else False):
            break

    return {
        "header_row_0": header_row_0,
        "student_start_0": student_start_0,
        "first_att_col_0": first_att_col_0,
    }


def get_students(service, spreadsheet_id: str, sheet_name: str) -> list[tuple[int, str]]:
    """Return list of (row_index_1based, full_name) for non-empty student rows."""
    start_row = _detect_student_start_row(service, spreadsheet_id, sheet_name)
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A{start_row}:C200"
    ).execute()
    rows = result.get("values", [])
    students = []
    for i, row in enumerate(rows, start=start_row):
        name = (row[1].strip() if len(row) > 1 else "") + " " + (row[2].strip() if len(row) > 2 else "")
        name = name.strip()
        if name and not name.startswith("❌"):
            students.append((i, name))
    return students


def _find_or_create_date_column(service, spreadsheet_id: str, sheet_name: str, sheet_id: int, today: datetime) -> int:
    """
    Find the column index (1-based) for today's date.
    Detects automatically which rows hold months / dates.
    """
    struct = _detect_structure(service, spreadsheet_id, sheet_name)
    month_row_0 = 0  # months are always in row 1
    date_row_0  = struct["header_row_0"]  # dates share the header row (or row 1 for פונקציונלי)
    # For פונקציונלי the dates are in row 2 (index 1), not the header row (index 0)
    if struct["header_row_0"] == 0:
        date_row_0 = 1

    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!1:{date_row_0 + 1}"
    ).execute()
    rows = result.get("values", [])
    row_months = rows[month_row_0] if month_row_0 < len(rows) else []
    row_dates  = rows[date_row_0]  if date_row_0  < len(rows) else []

    today_month = HEBREW_MONTHS[today.month]
    today_day   = str(today.day)

    current_month = None
    last_data_col = 0

    for col_idx, day_val in enumerate(row_dates):
        month_val = row_months[col_idx].strip() if col_idx < len(row_months) else ""
        if month_val and month_val not in ("שם", "שם משפחה", "מנוי", 'הו"ק', "משקל"):
            current_month = month_val.strip()
        if day_val and day_val not in ("שם", "שם משפחה", "מנוי", 'הו"ק', "משקל") and day_val.isdigit():
            last_data_col = col_idx + 1
            if current_month == today_month and day_val == today_day:
                return col_idx + 1, False  # existing column

    new_col = max(last_data_col + 1, struct["first_att_col_0"] + 1)
    requests = []

    # Write day number in the date row
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!R{date_row_0 + 1}C{new_col}",
        valueInputOption="RAW",
        body={"values": [[today_day]]}
    ).execute()

    # Write month header in row 1 if month changed
    if current_month != today_month:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!R1C{new_col}",
            valueInputOption="RAW",
            body={"values": [[today_month]]}
        ).execute()
        requests.append({
            "repeatCell": {
                "range": {"sheetId": sheet_id,
                           "startRowIndex": 0, "endRowIndex": 1,
                           "startColumnIndex": new_col - 1, "endColumnIndex": new_col},
                "cell": {"userEnteredFormat": {"backgroundColor": ORANGE_BG,
                          "textFormat": {"foregroundColor": _WHITE, "bold": True},
                          "horizontalAlignment": "CENTER"}},
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
            }
        })

    # Style the new date cell
    requests.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id,
                       "startRowIndex": date_row_0, "endRowIndex": date_row_0 + 1,
                       "startColumnIndex": new_col - 1, "endColumnIndex": new_col},
            "cell": {"userEnteredFormat": {"backgroundColor": _DATE_BG,
                      "textFormat": {"bold": True},
                      "horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
        }
    })
    # Set column width
    requests.append({
        "updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                       "startIndex": new_col - 1, "endIndex": new_col},
            "properties": {"pixelSize": 42}, "fields": "pixelSize",
        }
    })

    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests}
        ).execute()

    return new_col, True


def _get_sheet_id(service, spreadsheet_id: str, sheet_name: str) -> int:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] == sheet_name:
            return s["properties"]["sheetId"]
    raise ValueError(f"Sheet '{sheet_name}' not found")


def _is_attendance_color(bg: dict) -> bool:
    """Return True only if bg is green, red, or black (real attendance marks)."""
    if not bg:
        return False
    r = bg.get("red", 0.0)
    g = bg.get("green", 0.0)
    b = bg.get("blue", 0.0)
    if r < 0.15 and g > 0.85 and b < 0.15:   # green
        return True
    if r > 0.85 and g < 0.15 and b < 0.15:   # red
        return True
    if r < 0.15 and g < 0.15 and b < 0.15:   # black
        return True
    return False


def find_and_delete_empty_columns(service, spreadsheet_id: str, sheet_name: str,
                                   sheet_id: int, student_start_0: int,
                                   first_att_col_0: int) -> int:
    """
    Delete attendance columns that have no green/red/black cells.
    Only touches columns that also have a date value in the header rows.
    Returns number of columns deleted.
    """
    result = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        ranges=[sheet_name],
        includeGridData=True,
    ).execute()
    grid = result.get("sheets", [{}])[0].get("data", [{}])[0]
    all_rows = grid.get("rowData", [])
    if not all_rows:
        return 0

    # Find max column with actual date values (avoid deleting blank padding columns)
    date_cols = set()
    for row_0 in range(min(3, len(all_rows))):
        for col_0, cell in enumerate(all_rows[row_0].get("values", [])):
            val = cell.get("formattedValue", "").strip()
            if col_0 >= first_att_col_0 and val.isdigit():
                date_cols.add(col_0)

    empty_cols = []
    for col_0 in date_cols:
        has_attendance = False
        for row_0 in range(student_start_0, len(all_rows)):
            cells = all_rows[row_0].get("values", [])
            if col_0 >= len(cells):
                continue
            bg = cells[col_0].get("userEnteredFormat", {}).get("backgroundColor")
            if _is_attendance_color(bg):
                has_attendance = True
                break
        if not has_attendance:
            empty_cols.append(col_0)

    if not empty_cols:
        return 0

    # Delete in reverse order so indices stay valid
    requests = [{"deleteDimension": {
        "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                   "startIndex": col_0, "endIndex": col_0 + 1}
    }} for col_0 in sorted(empty_cols, reverse=True)]

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests}
    ).execute()
    return len(empty_cols)


def cleanup_all_empty_columns() -> dict:
    """
    Delete empty attendance columns from all branch sheets.
    Returns {branch: {group: count_deleted}}.
    """
    service = _get_service()
    results = {}
    for branch, spreadsheet_id in BRANCH_SHEETS.items():
        results[branch] = {}
        for group in BRANCH_GROUPS.get(branch, []):
            try:
                sheet_id = _get_sheet_id(service, spreadsheet_id, group)
                struct = _detect_structure(service, spreadsheet_id, group)
                deleted = find_and_delete_empty_columns(
                    service, spreadsheet_id, group, sheet_id,
                    struct["student_start_0"], struct["first_att_col_0"]
                )
                results[branch][group] = deleted
            except Exception:
                results[branch][group] = -1
    return results


def prepare_attendance(branch: str, group: str) -> dict:
    """
    Load students and find/create today's column.
    Returns a session dict to be stored in bot state.
    """
    service = _get_service()
    spreadsheet_id = BRANCH_SHEETS[branch]
    sheet_id = _get_sheet_id(service, spreadsheet_id, group)
    today = datetime.now()

    col, col_is_new = _find_or_create_date_column(service, spreadsheet_id, group, sheet_id, today)
    students = get_students(service, spreadsheet_id, group)

    return {
        "spreadsheet_id": spreadsheet_id,
        "sheet_name": group,
        "sheet_id": sheet_id,
        "col": col,
        "col_is_new": col_is_new,
        "students": students,
        "date": today.strftime("%d/%m/%Y"),
        "branch": branch,
    }


def _recolor_name_cols(service, spreadsheet_id: str, sheet_name: str, sheet_id: int):
    """Re-apply alternating blue to name columns (A-C) for all student rows."""
    students = get_students(service, spreadsheet_id, sheet_name)
    if not students:
        return
    req = []
    for i, (row, _) in enumerate(students):
        bg = _ROW_A if i % 2 == 0 else _ROW_B
        req.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": row - 1, "endRowIndex": row,
                       "startColumnIndex": 0, "endColumnIndex": 3},
            "cell": {"userEnteredFormat": {
                "backgroundColor": bg,
                "textFormat": {"fontSize": 11},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
        }})
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": req}
    ).execute()


def mark_attendance(session: dict, absent_indices: set[int]):
    """
    Mark attendance in the sheet.
    absent_indices: set of 1-based numbers from the displayed student list (1=first student).
    """
    service = _get_service()
    spreadsheet_id = session["spreadsheet_id"]
    sheet_id = session["sheet_id"]
    col = session["col"]
    students = session["students"]

    requests = []
    for list_num, (row, name) in enumerate(students, start=1):
        color = RED if list_num in absent_indices else GREEN
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row - 1,
                    "endRowIndex": row,
                    "startColumnIndex": col - 1,
                    "endColumnIndex": col,
                },
                "cell": {"userEnteredFormat": {"backgroundColor": color}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        })

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests}
    ).execute()

    # Full design refresh: headers, column widths, borders, blue rows
    try:
        apply_sheet_design(session["branch"], session["sheet_name"])
    except Exception:
        # Fallback: at least re-sync name column colors
        _recolor_name_cols(service, spreadsheet_id, session["sheet_name"], sheet_id)


def add_new_student(session: dict, first_name: str, last_name: str) -> tuple[int, str]:
    """
    Add a new student to the sheet after the last existing student.
    Mark today's column green for them.
    Returns (new_row, full_name).
    """
    service = _get_service()
    spreadsheet_id = session["spreadsheet_id"]
    sheet_name = session["sheet_name"]
    sheet_id = session["sheet_id"]
    col = session["col"]
    students = session["students"]

    full_name = f"{first_name} {last_name}".strip()

    # Prevent duplicate names
    existing_names = [n.strip().lower() for _, n in students]
    if full_name.lower() in existing_names:
        raise ValueError(f"הספורטאי {full_name} כבר קיים ברשימה")

    # Find next row and number
    last_row = students[-1][0] if students else 3
    new_row = last_row + 1
    new_num = len(students) + 1

    # Write number, first name, last name
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A{new_row}:C{new_row}",
        valueInputOption="RAW",
        body={"values": [[str(new_num), first_name, last_name]]}
    ).execute()

    # Mark green for today
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": new_row - 1,
                    "endRowIndex": new_row,
                    "startColumnIndex": col - 1,
                    "endColumnIndex": col,
                },
                "cell": {"userEnteredFormat": {"backgroundColor": GREEN}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        }]}
    ).execute()

    # Update session's student list
    session["students"].append((new_row, full_name))

    return new_row, full_name


def _renumber_students(service, spreadsheet_id: str, sheet_name: str):
    """Renumber students in column A starting from row 4."""
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!B4:B200"
    ).execute()
    rows = result.get("values", [])
    numbers = []
    num = 1
    for row in rows:
        name = row[0].strip() if row else ""
        numbers.append([str(num) if name else ""])
        if name:
            num += 1
    if numbers:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A4:A{3 + len(numbers)}",
            valueInputOption="RAW",
            body={"values": numbers}
        ).execute()


def _get_porshim_sheet_id(service, spreadsheet_id: str) -> int:
    """Return the sheetId of the פורשים tab."""
    result = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in result.get("sheets", []):
        if sheet["properties"]["title"] == "פורשים":
            return sheet["properties"]["sheetId"]
    raise ValueError("גיליון פורשים לא נמצא")


def _snapshot_row(service, spreadsheet_id: str, sheet_name: str, row_1based: int) -> list:
    """Capture all cell values and background colors for a row before deletion."""
    result = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        ranges=[f"{sheet_name}!A{row_1based}:ZZ{row_1based}"],
        includeGridData=True,
    ).execute()
    grid = result.get("sheets", [{}])[0].get("data", [{}])[0]
    row = grid.get("rowData", [{}])[0] if grid.get("rowData") else {}
    cells = []
    for cell in row.get("values", []):
        cells.append({
            "value": cell.get("userEnteredValue", {}),
            "bg": cell.get("userEnteredFormat", {}).get("backgroundColor"),
        })
    return cells


def clear_dropout_undo():
    """Clear the undo file — call at the start of each attendance save."""
    DROPOUT_UNDO_FILE.write_text("[]", encoding="utf-8")


def save_dropout_calendar_event(full_name: str, event_id: str):
    """After creating a calendar reminder, attach its event_id to the undo entry."""
    if not DROPOUT_UNDO_FILE.exists() or not event_id:
        return
    log = json.loads(DROPOUT_UNDO_FILE.read_text(encoding="utf-8"))
    for entry in log:
        if entry.get("full_name") == full_name:
            entry["calendar_event_id"] = event_id
            break
    DROPOUT_UNDO_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_dropout_undo(spreadsheet_id, sheet_name, sheet_id, row_1based, full_name, porshim_row, cells):
    log = json.loads(DROPOUT_UNDO_FILE.read_text(encoding="utf-8")) if DROPOUT_UNDO_FILE.exists() else []
    log.append({
        "spreadsheet_id": spreadsheet_id,
        "sheet_name": sheet_name,
        "sheet_id": sheet_id,
        "original_row": row_1based,
        "full_name": full_name,
        "porshim_row": porshim_row,
        "cells": cells,
    })
    DROPOUT_UNDO_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def undo_dropouts() -> list:
    """
    Restore all dropouts saved in the undo file.
    Returns list of restored student names.
    """
    if not DROPOUT_UNDO_FILE.exists():
        return []
    undos = json.loads(DROPOUT_UNDO_FILE.read_text(encoding="utf-8"))
    if not undos:
        return []

    service = _get_service()
    restored = []

    # Restore in original row order (ascending) — each insert shifts rows below
    for undo in sorted(undos, key=lambda x: x["original_row"]):
        sid = undo["spreadsheet_id"]
        sname = undo["sheet_name"]
        sheet_id = undo["sheet_id"]
        row = undo["original_row"]
        cells = undo["cells"]

        # Insert blank row at original position
        service.spreadsheets().batchUpdate(
            spreadsheetId=sid,
            body={"requests": [{"insertDimension": {
                "range": {"sheetId": sheet_id, "dimension": "ROWS",
                           "startIndex": row - 1, "endIndex": row},
                "inheritFromBefore": False,
            }}]}
        ).execute()

        # Write back values and background colors
        cell_data = []
        for c in cells:
            entry = {"userEnteredValue": c.get("value", {})}
            if c.get("bg"):
                entry["userEnteredFormat"] = {"backgroundColor": c["bg"]}
            cell_data.append(entry)

        service.spreadsheets().batchUpdate(
            spreadsheetId=sid,
            body={"requests": [{"updateCells": {
                "start": {"sheetId": sheet_id, "rowIndex": row - 1, "columnIndex": 0},
                "rows": [{"values": cell_data}],
                "fields": "userEnteredValue,userEnteredFormat.backgroundColor",
            }}]}
        ).execute()

        restored.append(undo["full_name"])

    # Delete from פורשים — group by spreadsheet, delete largest row first within each
    porshim_ids = {}  # spreadsheet_id → porshim sheetId (cached)
    for undo in sorted(undos, key=lambda x: x["porshim_row"], reverse=True):
        sid = undo["spreadsheet_id"]
        if sid not in porshim_ids:
            porshim_ids[sid] = _get_porshim_sheet_id(service, sid)
        service.spreadsheets().batchUpdate(
            spreadsheetId=sid,
            body={"requests": [{"deleteDimension": {
                "range": {"sheetId": porshim_ids[sid], "dimension": "ROWS",
                           "startIndex": undo["porshim_row"] - 1,
                           "endIndex": undo["porshim_row"]},
            }}]}
        ).execute()

    # Renumber active sheets + re-sync blue name columns
    seen = set()
    for undo in undos:
        key = (undo["spreadsheet_id"], undo["sheet_name"])
        if key not in seen:
            seen.add(key)
            _renumber_students(service, undo["spreadsheet_id"], undo["sheet_name"])
            _recolor_name_cols(service, undo["spreadsheet_id"], undo["sheet_name"], undo["sheet_id"])

    # Delete calendar reminders that were created for these dropouts
    import absence_tracker as _abt
    for undo in undos:
        event_id = undo.get("calendar_event_id")
        if event_id:
            _abt.delete_calendar_event(event_id)

    DROPOUT_UNDO_FILE.write_text("[]", encoding="utf-8")
    return restored


def mark_as_dropout(session: dict, student_index: int, start_date: str = ""):
    """
    Move a student to the פורשים sheet and remove them from the active sheet.
    student_index: 1-based index in session["students"].
    start_date: optional date the student started training.
    """
    service = _get_service()
    spreadsheet_id = session["spreadsheet_id"]
    sheet_name = session["sheet_name"]
    students = session["students"]

    row_1based, full_name = students[student_index - 1]
    parts = full_name.strip().split(" ", 1)
    first = parts[0]
    last = parts[1] if len(parts) > 1 else ""
    today = session.get("date") or datetime.now().strftime("%d/%m/%Y")

    # Snapshot the row BEFORE deletion (for undo)
    cells = _snapshot_row(service, spreadsheet_id, sheet_name, row_1based)

    # Append to פורשים: שם | שם משפחה | קבוצה | תאריך הצטרפות | תאריך פרישה
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range="פורשים!A:A"
    ).execute()
    porshim_row = len(result.get("values", [])) + 1
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"פורשים!A{porshim_row}:E{porshim_row}",
        valueInputOption="RAW",
        body={"values": [[first, last, sheet_name, start_date, today]]}
    ).execute()

    # Delete the physical row entirely (shifts all rows below up)
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{
            "deleteDimension": {
                "range": {
                    "sheetId": session["sheet_id"],
                    "dimension": "ROWS",
                    "startIndex": row_1based - 1,  # 0-based
                    "endIndex": row_1based,
                }
            }
        }]}
    ).execute()

    # Save undo data
    _append_dropout_undo(spreadsheet_id, sheet_name, session["sheet_id"],
                         row_1based, full_name, porshim_row, cells)

    # Renumber remaining students in column A
    _renumber_students(service, spreadsheet_id, sheet_name)

    return full_name


def get_dropouts(branch: str) -> list[dict]:
    """Return list of dropouts for a branch from the פורשים sheet."""
    service = _get_service()
    spreadsheet_id = BRANCH_SHEETS[branch]
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range="פורשים!A2:E200"
    ).execute()
    rows = result.get("values", [])
    dropouts = []
    for row in rows:
        if len(row) >= 2 and row[0].strip():
            dropouts.append({
                "name": (row[0] + " " + row[1]).strip(),
                "group": row[2] if len(row) > 2 else "",
                "start_date": row[3] if len(row) > 3 else "",
                "end_date": row[4] if len(row) > 4 else "",
            })
    return dropouts


def apply_sheet_design(branch: str, group: str):
    """
    Apply consistent visual design — auto-detects structure per sheet.
    1. Deletes empty (no attendance) columns first.
    2. Styles headers, name columns, borders, freeze, widths.
    Never touches attendance cell colors (green/red/black).
    """
    service = _get_service()
    spreadsheet_id = BRANCH_SHEETS[branch]
    sheet_id = _get_sheet_id(service, spreadsheet_id, group)

    struct = _detect_structure(service, spreadsheet_id, group)
    header_row_0    = struct["header_row_0"]
    student_start_0 = struct["student_start_0"]
    first_att_col_0 = struct["first_att_col_0"]

    # ── Step 1: delete empty attendance columns ───────────────────────────────
    find_and_delete_empty_columns(service, spreadsheet_id, group, sheet_id,
                                  student_start_0, first_att_col_0)

    # Re-detect structure after deletion (column count changed)
    struct = _detect_structure(service, spreadsheet_id, group)
    header_row_0    = struct["header_row_0"]
    student_start_0 = struct["student_start_0"]
    first_att_col_0 = struct["first_att_col_0"]
    first_att_col_0 = struct["first_att_col_0"]  # first attendance column (0-based)
    freeze_rows = header_row_0 + 2 if header_row_0 == 0 else header_row_0 + 1

    students = get_students(service, spreadsheet_id, group)
    last_student_row = students[-1][0] if students else student_start_0 + 10

    # Find last used column + reserve 20 extra for future dates
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{group}!{header_row_0 + 1}:{header_row_0 + 1}"
    ).execute()
    header_row_vals = result.get("values", [[]])[0]
    last_col = max(len(header_row_vals), first_att_col_0 + 1) + 20

    req = []

    if header_row_0 == 0:
        # ── פונקציונלי style: row 1 = name cols NAVY + attendance cols ORANGE ─
        req.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                       "startColumnIndex": 0, "endColumnIndex": first_att_col_0},
            "cell": {"userEnteredFormat": {
                "backgroundColor": _NAVY,
                "textFormat": {"foregroundColor": _WHITE, "bold": True, "fontSize": 11},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
        }})
        req.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                       "startColumnIndex": first_att_col_0, "endColumnIndex": last_col},
            "cell": {"userEnteredFormat": {
                "backgroundColor": ORANGE_BG,
                "textFormat": {"foregroundColor": _WHITE, "bold": True, "fontSize": 10},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
        }})
        # ── Row 2 = date numbers ──────────────────────────────────────────────
        req.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2,
                       "startColumnIndex": 0, "endColumnIndex": first_att_col_0},
            "cell": {"userEnteredFormat": {
                "backgroundColor": _NAVY,
                "textFormat": {"foregroundColor": _WHITE, "bold": True, "fontSize": 10},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
        }})
        req.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2,
                       "startColumnIndex": first_att_col_0, "endColumnIndex": last_col},
            "cell": {"userEnteredFormat": {
                "backgroundColor": _DATE_BG,
                "textFormat": {"bold": True, "fontSize": 10},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
        }})
    else:
        # ── Standard style: row 1 = months ORANGE, header_row = NAVY + dates ─
        req.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                       "startColumnIndex": first_att_col_0, "endColumnIndex": last_col},
            "cell": {"userEnteredFormat": {
                "backgroundColor": ORANGE_BG,
                "textFormat": {"foregroundColor": _WHITE, "bold": True, "fontSize": 10},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
        }})
        # Safety: never paint NAVY on student rows — clamp to student_start_0 - 1
        safe_header_end = min(header_row_0 + 1, student_start_0)
        req.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": header_row_0, "endRowIndex": safe_header_end,
                       "startColumnIndex": 0, "endColumnIndex": last_col},
            "cell": {"userEnteredFormat": {
                "backgroundColor": _NAVY,
                "textFormat": {"foregroundColor": _WHITE, "bold": True, "fontSize": 11},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
        }})
        req.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": header_row_0, "endRowIndex": safe_header_end,
                       "startColumnIndex": first_att_col_0, "endColumnIndex": last_col},
            "cell": {"userEnteredFormat": {
                "backgroundColor": _DATE_BG,
                "textFormat": {"foregroundColor": {"red": 0.1, "green": 0.1, "blue": 0.1},
                               "bold": True, "fontSize": 10},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
        }})

    # ── Clear gap rows between header and first student (e.g. row 3 in א-ב) ────
    if student_start_0 > header_row_0 + 1:
        req.append({"repeatCell": {
            "range": {"sheetId": sheet_id,
                       "startRowIndex": header_row_0 + 1, "endRowIndex": student_start_0,
                       "startColumnIndex": 0, "endColumnIndex": last_col},
            "cell": {"userEnteredFormat": {
                "backgroundColor": _WHITE,
            }},
            "fields": "userEnteredFormat.backgroundColor",
        }})

    # ── Student rows: alternating blue on name columns (A-C) ─────────────────
    for i, (row, _) in enumerate(students):
        bg = _ROW_A if i % 2 == 0 else _ROW_B
        req.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": row - 1, "endRowIndex": row,
                       "startColumnIndex": 0, "endColumnIndex": 3},
            "cell": {"userEnteredFormat": {
                "backgroundColor": bg,
                "textFormat": {"fontSize": 11},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
        }})

    # ── Attendance cells: alignment only — NO color changes ───────────────────
    if students:
        req.append({"repeatCell": {
            "range": {"sheetId": sheet_id,
                       "startRowIndex": students[0][0] - 1, "endRowIndex": last_student_row,
                       "startColumnIndex": first_att_col_0, "endColumnIndex": last_col},
            "cell": {"userEnteredFormat": {
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(horizontalAlignment,verticalAlignment)",
        }})

    # ── Borders ───────────────────────────────────────────────────────────────
    thin  = {"style": "SOLID",        "width": 1, "color": _BORDER}
    thick = {"style": "SOLID_MEDIUM", "width": 2, "color": _BORDER_OUTER}
    req.append({"updateBorders": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": last_student_row,
                   "startColumnIndex": 0, "endColumnIndex": last_col},
        "innerHorizontal": thin, "innerVertical": thin,
        "top": thick, "bottom": thick, "left": thick, "right": thick,
    }})
    # Bold separator between name cols and attendance cols
    req.append({"updateBorders": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": last_student_row,
                   "startColumnIndex": first_att_col_0, "endColumnIndex": first_att_col_0 + 1},
        "left": {"style": "SOLID_MEDIUM", "width": 2, "color": _NAVY},
    }})

    # ── Freeze rows + cols ────────────────────────────────────────────────────
    req.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": freeze_rows, "frozenColumnCount": 3}},
        "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
    }})

    # ── Column widths ─────────────────────────────────────────────────────────
    for start, end, px in [(0, 1, 50), (1, 2, 110), (2, 3, 130)]:
        req.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                       "startIndex": start, "endIndex": end},
            "properties": {"pixelSize": px}, "fields": "pixelSize",
        }})
    if last_col > 3:
        req.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                       "startIndex": first_att_col_0, "endIndex": last_col},
            "properties": {"pixelSize": 42}, "fields": "pixelSize",
        }})

    # ── Row heights ───────────────────────────────────────────────────────────
    req.append({"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "ROWS",
                   "startIndex": 0, "endIndex": last_student_row},
        "properties": {"pixelSize": 28}, "fields": "pixelSize",
    }})

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": req}
    ).execute()


def cancel_attendance(session: dict):
    """
    Cancel attendance session.
    If column was newly created today — delete it physically.
    If column already existed — just clear today's colors.
    """
    service = _get_service()
    spreadsheet_id = session["spreadsheet_id"]
    sheet_id = session["sheet_id"]
    col = session["col"]
    students = session["students"]

    if session.get("col_is_new"):
        # Delete the column entirely — restores sheet to pre-session state
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"deleteDimension": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                           "startIndex": col - 1, "endIndex": col},
            }}]}
        ).execute()
    else:
        # Column existed before — just clear the colors we may have toggled
        requests = []
        for row, _ in students:
            requests.append({"updateCells": {
                "range": {"sheetId": sheet_id,
                           "startRowIndex": row - 1, "endRowIndex": row,
                           "startColumnIndex": col - 1, "endColumnIndex": col},
                "rows": [{"values": [{"userEnteredFormat": {}}]}],
                "fields": "userEnteredFormat.backgroundColor",
            }})
        if requests:
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": requests}
            ).execute()

def _search_students_in_group(service, spreadsheet_id, group):
    """קרא את כל שורות הספורטאים (כולל לא פעילים) מקבוצה."""
    try:
        start_row = _detect_student_start_row(service, spreadsheet_id, group)
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{group}!A{start_row}:C200"
        ).execute()
        return start_row, result.get("values", [])
    except Exception:
        return None, []


def deactivate_student(branch: str, student_name: str) -> str:
    """סמן ספורטאי כלא פעיל — מוסיף ❌ לתחילת עמודה B."""
    spreadsheet_id = BRANCH_SHEETS.get(branch)
    if not spreadsheet_id:
        return f"❌ סניף לא מוכר: {branch}"

    service = _get_service()
    groups = BRANCH_GROUPS.get(branch, [])
    found = []
    name_lower = student_name.strip().lower()

    for group in groups:
        start_row, all_rows = _search_students_in_group(service, spreadsheet_id, group)
        if start_row is None:
            continue

        for i, row in enumerate(all_rows, start=start_row):
            b = row[1].strip() if len(row) > 1 else ""
            c = row[2].strip() if len(row) > 2 else ""
            if b.startswith("❌"):
                continue  # כבר לא פעיל
            full_name = (b + " " + c).strip()
            if name_lower in full_name.lower():
                service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=f"'{group}'!B{i}",
                    valueInputOption="RAW",
                    body={"values": [["❌ " + b]]}
                ).execute()
                found.append(f"{group}: {full_name}")

    if found:
        return "✅ סומן כלא פעיל:\n" + "\n".join(f"  • {f}" for f in found)
    return f"⚠️ לא נמצא '{student_name}' בסניף {branch}"


def activate_student(branch: str, student_name: str) -> str:
    """החזר ספורטאי לפעיל — מסיר ❌ מעמודה B."""
    spreadsheet_id = BRANCH_SHEETS.get(branch)
    if not spreadsheet_id:
        return f"❌ סניף לא מוכר: {branch}"

    service = _get_service()
    groups = BRANCH_GROUPS.get(branch, [])
    found = []
    name_lower = student_name.strip().lower()

    for group in groups:
        start_row, all_rows = _search_students_in_group(service, spreadsheet_id, group)
        if start_row is None:
            continue

        for i, row in enumerate(all_rows, start=start_row):
            b = row[1].strip() if len(row) > 1 else ""
            c = row[2].strip() if len(row) > 2 else ""
            if not b.startswith("❌"):
                continue
            clean_b = b[1:].strip()  # הסר ❌ ורווח
            full_name = (clean_b + " " + c).strip()
            if name_lower in full_name.lower():
                resp = service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=f"'{group}'!B{i}",
                    valueInputOption="RAW",
                    body={"values": [[clean_b]]}
                ).execute()
                updated = resp.get("updatedCells", 0)
                found.append(f"{group}: {full_name} (עודכנו {updated} תאים)")

    if found:
        return "✅ הוחזר לפעיל:\n" + "\n".join(f"  • {f}" for f in found)
    return f"⚠️ לא נמצא '{student_name}' מסומן כלא פעיל בסניף {branch}"
