"""
Save training plans directly to the Google Sheets training plans spreadsheet.
Sheet ID: 1hi073ueyzdzEjzhP6a3ZgTPpeZDNzH2g2rKPj-L8a6I
Structure: row1 = headers (שעה, קבוצה, date1, date2...), then group blocks with content rows.
"""

import os, pickle, base64, warnings
from datetime import date as date_cls
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

ALL_TABS = list(BRANCH_TABS.values())

# ── Color palette ──────────────────────────────────────────────────────────────
_NAVY       = {"red": 0.13, "green": 0.19, "blue": 0.36}
_WHITE      = {"red": 1.0,  "green": 1.0,  "blue": 1.0}
_DATE_BG    = {"red": 0.82, "green": 0.87, "blue": 0.95}   # past date header
_LAST_HDR   = {"red": 0.98, "green": 0.60, "blue": 0.12}   # latest plan header (orange)
_LAST_CELL  = {"red": 1.00, "green": 0.95, "blue": 0.80}   # latest plan content cells
_GROUP_A    = {"red": 0.18, "green": 0.39, "blue": 0.60}   # group header shade 1
_GROUP_B    = {"red": 0.24, "green": 0.48, "blue": 0.68}   # group header shade 2
_ROW_A      = {"red": 0.95, "green": 0.97, "blue": 1.00}
_ROW_B      = {"red": 1.00, "green": 1.00, "blue": 1.00}
_BORDER     = {"red": 0.7,  "green": 0.7,  "blue": 0.8}
_BLACK      = {"red": 0.0,  "green": 0.0,  "blue": 0.0}


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


def _col_letter(col_0: int) -> str:
    result = ""
    col_0 += 1
    while col_0 > 0:
        col_0, remainder = divmod(col_0 - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _parse_date(cell: str):
    """Parse D/M or D/M/YYYY date string."""
    import re
    m = re.match(r'(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?', cell.strip())
    if not m:
        return None
    d, mo = int(m.group(1)), int(m.group(2))
    y = int(m.group(3)) if m.group(3) else date_cls.today().year
    if y < 100:
        y += 2000
    try:
        return date_cls(y, mo, d)
    except ValueError:
        return None


def _find_empty_date_cols(rows: list, header: list) -> list[int]:
    """Return 0-based indices of date columns (col>=2) where ALL content rows are empty."""
    empty = []
    for c in range(2, len(header)):
        has_content = any(c < len(row) and row[c].strip() for row in rows[1:])
        if not has_content:
            empty.append(c)
    return empty


def _delete_columns(service, sheet_id: int, col_indices: list[int]):
    """Delete columns by 0-based index, right-to-left so indices stay valid."""
    for c in sorted(col_indices, reverse=True):
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{"deleteDimension": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": c, "endIndex": c + 1}
            }}]}
        ).execute()


def _find_group_rows(rows: list) -> list[tuple[int, int, str]]:
    """Return list of (start_row_0, end_row_0_excl, group_name) for each group block."""
    blocks = []
    block_start = None
    group_name = ""
    for i, row in enumerate(rows):
        if len(row) >= 2 and row[1].strip():
            if block_start is not None:
                blocks.append((block_start, i, group_name))
            block_start = i
            group_name = row[1].strip()
        elif block_start is None:
            pass  # skip header row
    if block_start is not None:
        blocks.append((block_start, len(rows), group_name))
    return blocks


def _repeat_cell(sheet_id, r1, r2, c1, c2, fmt):
    return {"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": r1, "endRowIndex": r2,
                  "startColumnIndex": c1, "endColumnIndex": c2},
        "cell": {"userEnteredFormat": fmt},
        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
    }}


def _border_range(sheet_id, r1, r2, c1, c2):
    b = {"style": "SOLID", "color": _BORDER}
    return {"updateBorders": {
        "range": {"sheetId": sheet_id, "startRowIndex": r1, "endRowIndex": r2,
                  "startColumnIndex": c1, "endColumnIndex": c2},
        "top": b, "bottom": b, "left": b, "right": b,
        "innerHorizontal": b, "innerVertical": b,
    }}


def _col_width(sheet_id, c1, c2, px):
    return {"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                  "startIndex": c1, "endIndex": c2},
        "properties": {"pixelSize": px}, "fields": "pixelSize",
    }}


def _row_height(sheet_id, r1, r2, px):
    return {"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "ROWS",
                  "startIndex": r1, "endIndex": r2},
        "properties": {"pixelSize": px}, "fields": "pixelSize",
    }}


def _freeze(sheet_id, rows=1, cols=2):
    return {"updateSheetProperties": {
        "properties": {"sheetId": sheet_id,
                       "gridProperties": {"frozenRowCount": rows, "frozenColumnCount": cols}},
        "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"
    }}


def design_tab(service, tab_name: str, sheet_id: int, delete_empty: bool = True) -> int:
    """
    Full design for one training-plan tab.
    - Deletes empty date columns
    - Highlights today's column in green
    - Applies consistent styling to headers, group rows, content rows
    Returns number of empty columns deleted.
    """
    rows = _read_tab(service, tab_name)
    if not rows:
        return 0

    header = rows[0]
    today = date_cls.today()
    today_str = f"{today.day}/{today.month}"

    # ── Delete empty date columns ──────────────────────────────────────────────
    deleted = 0
    if delete_empty:
        empty_cols = _find_empty_date_cols(rows, header)
        if empty_cols:
            _delete_columns(service, sheet_id, empty_cols)
            deleted = len(empty_cols)
            # Re-read after deletion
            rows = _read_tab(service, tab_name)
            if not rows:
                return deleted
            header = rows[0]

    n_cols = max(len(r) for r in rows) if rows else 3
    n_rows = len(rows)

    # ── Find last column that has ANY content in body rows ─────────────────────
    last_filled_col = None
    for c in range(n_cols - 1, 1, -1):
        if any(c < len(row) and row[c].strip() for row in rows[1:]):
            last_filled_col = c
            break

    group_blocks = _find_group_rows(rows[1:])  # skip header
    # Adjust indices: rows[1:] offset
    group_blocks = [(g[0] + 1, g[1] + 1, g[2]) for g in group_blocks]

    requests = []

    # Freeze
    requests.append(_freeze(sheet_id, rows=1, cols=2))

    # Column widths
    requests.append(_col_width(sheet_id, 0, 1, 95))   # שעה
    requests.append(_col_width(sheet_id, 1, 2, 80))   # קבוצה
    if n_cols > 2:
        requests.append(_col_width(sheet_id, 2, n_cols, 120))
    # Make last filled column slightly wider so it stands out
    if last_filled_col:
        requests.append(_col_width(sheet_id, last_filled_col, last_filled_col + 1, 140))

    # Row heights
    requests.append(_row_height(sheet_id, 0, n_rows, 34))

    # ── Header row (row 0): שעה + קבוצה = navy, dates = blue, last = orange ────
    requests.append(_repeat_cell(sheet_id, 0, 1, 0, 2, {
        "backgroundColor": _NAVY,
        "textFormat": {"bold": True, "fontSize": 11, "foregroundColor": _WHITE},
        "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
        "wrapStrategy": "WRAP",
    }))
    for c in range(2, n_cols):
        is_last = (last_filled_col is not None and c == last_filled_col)
        bg = _LAST_HDR if is_last else _DATE_BG
        txt_color = _WHITE if is_last else _BLACK
        fsize = 11 if is_last else 10
        requests.append(_repeat_cell(sheet_id, 0, 1, c, c + 1, {
            "backgroundColor": bg,
            "textFormat": {"bold": True, "fontSize": fsize, "foregroundColor": txt_color},
            "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            "wrapStrategy": "WRAP",
        }))

    # ── Group blocks ───────────────────────────────────────────────────────────
    for idx, (g_start, g_end, _) in enumerate(group_blocks):
        g_color = _GROUP_A if idx % 2 == 0 else _GROUP_B

        # Group header row
        requests.append(_repeat_cell(sheet_id, g_start, g_start + 1, 0, n_cols, {
            "backgroundColor": g_color,
            "textFormat": {"bold": True, "fontSize": 10, "foregroundColor": _WHITE},
            "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            "wrapStrategy": "WRAP",
        }))

        # Content rows
        for r in range(g_start + 1, g_end):
            row_bg = _ROW_A if (r - g_start) % 2 == 0 else _ROW_B
            # name cols (A, B) — center
            requests.append(_repeat_cell(sheet_id, r, r + 1, 0, 2, {
                "backgroundColor": row_bg,
                "textFormat": {"fontSize": 10},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                "wrapStrategy": "WRAP",
            }))
            # date content cols
            for c in range(2, n_cols):
                is_last = (last_filled_col is not None and c == last_filled_col)
                bg = _LAST_CELL if is_last else row_bg
                bold = is_last
                requests.append(_repeat_cell(sheet_id, r, r + 1, c, c + 1, {
                    "backgroundColor": bg,
                    "textFormat": {"fontSize": 10, "bold": bold},
                    "horizontalAlignment": "RIGHT", "verticalAlignment": "MIDDLE",
                    "wrapStrategy": "WRAP",
                }))

    # ── Borders ────────────────────────────────────────────────────────────────
    if n_rows > 0 and n_cols > 0:
        requests.append(_border_range(sheet_id, 0, n_rows, 0, n_cols))

    # Send in chunks
    for i in range(0, len(requests), 400):
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": requests[i:i + 400]}
        ).execute()

    return deleted


def design_all_tabs(delete_empty: bool = True) -> str:
    """Design all training plan tabs. Returns summary string."""
    import time
    service = _get_service()
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    tabs = [(s["properties"]["title"], s["properties"]["sheetId"]) for s in meta["sheets"]]

    results = []
    for tab_name, sid in tabs:
        try:
            deleted = design_tab(service, tab_name, sid, delete_empty)
            msg = f"✅ {tab_name}"
            if deleted:
                msg += f" (נמחקו {deleted} עמודות ריקות)"
            results.append(msg)
        except Exception as e:
            results.append(f"❌ {tab_name}: {e}")
        time.sleep(1.5)  # avoid quota exceeded

    return "\n".join(results)


_ORANGE   = {"red": 0.976, "green": 0.600, "blue": 0.118}  # #f9991e — כותרת תאריך חדש
_CREAM    = {"red": 1.0,   "green": 0.949, "blue": 0.800}  # #fff2cc — שורות תוכן
_WHITE_FG = {"red": 1.0,   "green": 1.0,   "blue": 1.0}
_BLACK_FG = {"red": 0.0,   "green": 0.0,   "blue": 0.0}


def _find_or_create_date_col(service, tab_name: str, plan_date) -> int:
    """Return 0-based column index for the given date, creating it if needed."""
    rows = _read_tab(service, tab_name)
    if not rows:
        raise ValueError("גיליון ריק")
    header = rows[0]
    date_str = f"{plan_date.day}/{plan_date.month}"

    for i, cell in enumerate(header):
        if cell.strip() == date_str:
            return i

    # Add new column at end
    new_col = max(len(header), 2)
    col_letter = _col_letter(new_col)
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{tab_name}'!{col_letter}1",
        valueInputOption="RAW",
        body={"values": [[date_str]]}
    ).execute()

    # Apply formatting: orange header + cream content rows
    sheet_id = _get_sheet_id(service, tab_name)
    n_rows = len(rows) + 1
    requests = [
        # כותרת תאריך — כתום, לבן, bold, 11
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": new_col, "endColumnIndex": new_col + 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": _ORANGE,
                "textFormat": {"bold": True, "fontSize": 11, "foregroundColor": _WHITE_FG},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
        }},
        # שורות תוכן — צהוב-שמנת, שחור, bold
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": n_rows,
                      "startColumnIndex": new_col, "endColumnIndex": new_col + 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": _CREAM,
                "textFormat": {"bold": True, "fontSize": 10, "foregroundColor": _BLACK_FG},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                "wrapStrategy": "WRAP",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
        }},
    ]
    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"requests": requests}
    ).execute()

    return new_col


def _find_group_rows_for_group(rows: list, group_keyword: str) -> list[int]:
    """Return 0-based row indices that belong to a group block matching keyword."""
    group_keyword = group_keyword.strip().replace("–", "-").replace("—", "-")
    block_start = None
    block_rows = []

    for i, row in enumerate(rows):
        if len(row) >= 2 and row[1].strip():
            if block_start is not None:
                cell = rows[block_start][1].strip().replace("–", "-").replace("—", "-")
                if group_keyword in cell or cell in group_keyword:
                    return block_rows
            block_start = i
            block_rows = [i]
        elif block_start is not None:
            block_rows.append(i)

    if block_start is not None:
        cell = rows[block_start][1].strip().replace("–", "-").replace("—", "-")
        if group_keyword in cell or cell in group_keyword:
            return block_rows
    return []


def save_plan_to_sheet(branch: str, group: str, plan_date, plan_items: list[str]) -> str:
    """
    Write plan_items into the training plans sheet for the given branch/group/date.
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
    group_rows = _find_group_rows_for_group(rows, group)

    if not group_rows:
        raise ValueError(f"קבוצה '{group}' לא נמצאה בלשונית {tab_name}")

    updates = []
    for i, item in enumerate(plan_items[:len(group_rows)]):
        row_1 = group_rows[i] + 1
        updates.append({
            "range": f"'{tab_name}'!{col_letter}{row_1}",
            "values": [[item]]
        })

    if updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"valueInputOption": "RAW", "data": updates}
        ).execute()

    # Refresh design after save
    design_tab(service, tab_name, sheet_id, delete_empty=False)

    date_str = f"{plan_date.day}/{plan_date.month}"
    return f"✅ נשמר בגיליון {tab_name} — {group} — {date_str} ({len(updates)} שורות)"


def save_multigroup_plan(branch: str, plan_date, groups: list[dict]) -> str:
    """
    Save multiple groups at once.
    groups = [{"group": "ד-ו", "items": ["חימום...", "תרגול...", ...]}, ...]
    Returns summary string.
    """
    results = []
    for g in groups:
        try:
            msg = save_plan_to_sheet(branch, g["group"], plan_date, g["items"])
            results.append(msg)
        except ValueError as e:
            # Group not found in this tab — skip silently with note
            results.append(f"⚠️ {g['group']}: לא קיים בטאב {branch} — דולג")
        except Exception as e:
            results.append(f"❌ {g['group']}: {e}")
    return "\n".join(results)


def parse_multigroup_text(text: str) -> tuple:
    """
    Parse a multi-group training plan message.
    Detects branch from context, extracts groups with their content lines.
    Returns (branch_or_None, [{"group": ..., "time": ..., "items": [...]}, ...])
    """
    import re

    groups = []
    current_group = None

    # Detect branch from text — prefer longer/more specific matches
    branch = None
    for b in sorted(BRANCH_TABS, key=len, reverse=True):
        # Only match branch names that appear outside group context
        if b in text and b not in ["נבחרת"]:  # נבחרת is also a group name
            branch = b
            break
    if not branch and "נבחרת" in text and "סירקין" not in text:
        branch = "נבחרת"

    lines = text.splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Header line: contains ⏰ or 👥 or time pattern + group
        time_match = re.search(r'(\d{1,2}:\d{2})', line)
        group_match = re.search(
            r'👥\s*\*?\*?([א-תa-zA-Z\d\-–— "\']+?)(?:\s*[\(\*]|$)', line
        )

        if time_match and ('👥' in line or '|' in line):
            if current_group:
                groups.append(current_group)
            group_name = ""
            if group_match:
                group_name = group_match.group(1).strip().rstrip("*").strip()
            # Clean up group name
            group_name = re.sub(r'\s*[\(\[].*', '', group_name).strip()
            current_group = {
                "group": group_name,
                "time":  time_match.group(1),
                "items": [],
            }
            continue

        # Content line
        if current_group is not None:
            # Skip "נושא:" prefix lines — use as first item
            if line.startswith("נושא:"):
                topic = line.replace("נושא:", "").strip()
                if topic:
                    current_group["items"].append(topic)
            elif line.startswith("•") or line.startswith("-") or line.startswith("*"):
                item = re.sub(r'^[•\-\*]\s*', '', line).strip()
                if item:
                    current_group["items"].append(item)

    if current_group:
        groups.append(current_group)

    return branch, groups


def is_multigroup_plan(text: str) -> bool:
    """Returns True if text looks like a multi-group training plan."""
    import re
    time_count = len(re.findall(r'⏰|👥|\d{1,2}:\d{2}\s*\|', text))
    bullet_count = len(re.findall(r'^[•\-]', text, re.MULTILINE))
    return time_count >= 2 and bullet_count >= 3
