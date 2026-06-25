"""
Save training plans directly to the Google Sheets training plans spreadsheet.
Sheet ID: 1hi073ueyzdzEjzhP6a3ZgTPpeZDNzH2g2rKPj-L8a6I
Structure: row1 = headers (שעה, קבוצה, date1, date2...), then group blocks with content rows.
"""

import os, pickle, base64, warnings, re
from datetime import datetime, date
warnings.filterwarnings("ignore")
import googleapiclient.discovery

SPREADSHEET_ID = "1hi073ueyzdzEjzhP6a3ZgTPpeZDNzH2g2rKPj-L8a6I"

BRANCH_TABS = {
    "סירקין":     "סירקין",
    "חגור":       "חגור",
    "נווה ירק":   "נווה ירק",
    "אהרונוביץ":  "אהרונוביץ",
    "איפון פייט": "איפון פייט",
    "פונקציונלי": "פונקציונאלי ",
    "נבחרת":      "נבחרת",
}

_HEADER_BG  = {"red": 0.13, "green": 0.19, "blue": 0.36}
_WHITE      = {"red": 1.0,  "green": 1.0,  "blue": 1.0}
_DATE_BG    = {"red": 0.82, "green": 0.85, "blue": 0.89}
_ROW_A      = {"red": 0.95, "green": 0.96, "blue": 1.00}
_ROW_B      = {"red": 1.00, "green": 1.00, "blue": 1.00}


def _get_service():
    b64 = os.environ.get("GOOGLE_CREDS_B64")
    if b64:
        creds = pickle.loads(base64.b64decode(b64))
    else:
        with open(os.path.expanduser("~/.wolves_judo_token.pickle"), "rb") as f:
            creds = pickle.load(f)
    return googleapiclient.discovery.build("sheets", "v4", credentials=creds)


def _get_sheet_id(service, tab_name: str) -> int:
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"].strip() == tab_name.strip():
            return s["properties"]["sheetId"]
    raise ValueError(f"לשונית לא נמצאה: {tab_name}")


def _read_tab(service, tab_name: str) -> list:
    res = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{tab_name}'!A1:ZZ200"
    ).execute()
    return res.get("values", [])


def _find_or_create_date_col(service, tab_name: str, plan_date: date) -> int:
    """Return 0-based column index for the given date, creating it if needed."""
    rows = _read_tab(service, tab_name)
    if not rows:
        raise ValueError("גיליון ריק")
    header = rows[0]
    date_str = f"{plan_date.day}/{plan_date.month}"

    # Search for existing column
    for i, cell in enumerate(header):
        if cell.strip() == date_str:
            return i

    # Add new column at end
    new_col = max(len(header), 2)
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{tab_name}'!{_col_letter(new_col)}1",
        valueInputOption="RAW",
        body={"values": [[date_str]]}
    ).execute()
    return new_col


def _col_letter(col_0: int) -> str:
    """Convert 0-based column index to A, B, ... Z, AA, AB..."""
    result = ""
    col_0 += 1
    while col_0 > 0:
        col_0, remainder = divmod(col_0 - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _find_group_rows(rows: list, group_keyword: str) -> list:
    """Return 0-based row indices that belong to a group block matching keyword."""
    group_keyword = group_keyword.strip()
    block_start = None
    block_rows = []

    for i, row in enumerate(rows):
        if len(row) >= 2 and row[1].strip():
            # Start of a new group block
            if block_start is not None:
                if _group_matches(rows[block_start][1], group_keyword):
                    return block_rows
            block_start = i
            block_rows = [i]
        elif block_start is not None:
            block_rows.append(i)

    # Last block
    if block_start is not None and _group_matches(rows[block_start][1], group_keyword):
        return block_rows

    return []


def _group_matches(cell: str, keyword: str) -> bool:
    cell = cell.strip().replace("–", "-").replace("—", "-")
    keyword = keyword.strip().replace("–", "-").replace("—", "-")
    return keyword in cell or cell in keyword


def save_plan_to_sheet(branch: str, group: str, plan_date: date, plan_items: list[str]) -> str:
    """
    Write plan_items into the training plans sheet for the given branch/group/date.
    plan_items: list of strings, one per row in the group block.
    Returns a summary string.
    """
    tab_name = BRANCH_TABS.get(branch)
    if not tab_name:
        raise ValueError(f"סניף לא מוכר: {branch}")

    service = _get_service()
    sheet_id = _get_sheet_id(service, tab_name)
    col_0 = _find_or_create_date_col(service, tab_name, plan_date)
    col_letter = _col_letter(col_0)

    rows = _read_tab(service, tab_name)
    group_rows = _find_group_rows(rows, group)

    if not group_rows:
        raise ValueError(f"קבוצה '{group}' לא נמצאה בלשונית {tab_name}")

    # Write items into the group rows (skip first row if it already has שעה)
    updates = []
    for i, item in enumerate(plan_items[:len(group_rows)]):
        row_0 = group_rows[i]
        row_1 = row_0 + 1
        updates.append({
            "range": f"'{tab_name}'!{col_letter}{row_1}",
            "values": [[item]]
        })

    if updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"valueInputOption": "RAW", "data": updates}
        ).execute()

    # Style the new date column header
    req = [{"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                   "startColumnIndex": col_0, "endColumnIndex": col_0 + 1},
        "cell": {"userEnteredFormat": {
            "backgroundColor": _DATE_BG,
            "textFormat": {"bold": True, "fontSize": 10},
            "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
    }}]
    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID, body={"requests": req}
    ).execute()

    date_str = f"{plan_date.day}/{plan_date.month}"
    return f"✅ נשמר בגיליון {tab_name} — {group} — {date_str} ({len(updates)} שורות)"
