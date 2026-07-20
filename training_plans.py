"""
Save training plans directly to the Google Sheets training plans spreadsheet.
Sheet ID: 1hi073ueyzdzEjzhP6a3ZgTPpeZDNzH2g2rKPj-L8a6I
Structure: row1 = headers (שעה, קבוצה, date1, date2...), then group blocks with content rows.
"""

import os, pickle, base64, warnings, json, sys
from datetime import date as date_cls
from pathlib import Path
warnings.filterwarnings("ignore")
import googleapiclient.discovery
import anthropic

_claude = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

# ── Logging ──
_LOG_FILE = Path(os.path.expanduser("~/logs")) / "training_plans_sync.log"
_LOG_FILE.parent.mkdir(exist_ok=True)

def _log(msg: str):
    """Log to file and print."""
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass
    print(line, file=sys.stderr)

# Row types in order — maps to the 6 rows of each group block in the sheet
ROW_TYPES = ["חימום", "תרגול", "קרבות", "משחק", "כוח", "נוסף"]

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
_NAVY        = {"red": 0.10, "green": 0.16, "blue": 0.32}   # כותרת שעה/קבוצה
_WHITE       = {"red": 1.0,  "green": 1.0,  "blue": 1.0}
_BLACK       = {"red": 0.0,  "green": 0.0,  "blue": 0.0}
_BORDER      = {"red": 0.65, "green": 0.65, "blue": 0.75}

# עמודות עבר — כחול כהה
_PAST_HDR    = {"red": 0.12, "green": 0.28, "blue": 0.53}   # כותרת כחול כהה
_PAST_CELL   = {"red": 0.82, "green": 0.89, "blue": 0.97}   # תא כחול בהיר

# עמודת היום — כתום בוהק
_TODAY_HDR   = {"red": 0.95, "green": 0.45, "blue": 0.05}   # כותרת כתום חזק
_TODAY_CELL  = {"red": 1.00, "green": 0.96, "blue": 0.72}   # תא צהוב-קרם

# עמודת עתיד / אחרון — כתום עדין
_FUTURE_HDR  = {"red": 0.98, "green": 0.60, "blue": 0.12}   # כתום בינוני
_FUTURE_CELL = {"red": 1.00, "green": 0.97, "blue": 0.84}   # קרם חם

# עמודות ריקות / לא-תאריך — ניטרלי, לא כתום
_EMPTY_HDR   = {"red": 0.85, "green": 0.85, "blue": 0.85}
_EMPTY_CELL  = {"red": 0.96, "green": 0.96, "blue": 0.96}

# קבוצות
_GROUP_A     = {"red": 0.15, "green": 0.35, "blue": 0.58}
_GROUP_B     = {"red": 0.22, "green": 0.44, "blue": 0.66}

# גווני כתום לשורות קבוצה בעמודת "last"
_GROUP_LAST_A = {"red": 0.75, "green": 0.32, "blue": 0.04}   # כתום כהה
_GROUP_LAST_B = {"red": 0.85, "green": 0.42, "blue": 0.05}   # כתום בינוני-כהה
_ROW_A       = {"red": 0.94, "green": 0.96, "blue": 1.00}
_ROW_B       = {"red": 1.00, "green": 1.00, "blue": 1.00}

# Legacy aliases (used by _find_or_create_date_col)
_DATE_BG    = _PAST_HDR
_LAST_HDR   = _FUTURE_HDR
_LAST_CELL  = _FUTURE_CELL


def _get_service():
    b64 = os.environ.get("GOOGLE_CREDS_B64")
    if b64:
        creds = pickle.loads(base64.b64decode(b64 + "=="))
    else:
        with open(os.path.expanduser("~/token.pickle"), "rb") as f:
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
    """
    Parse D/M or D/M/YYYY date string.
    When year is absent, infers the correct season year:
      season Sep Y → Jul Y+1. Today in Jan-Aug → Sep-Dec belong to Y-1.
    """
    import re
    m = re.match(r'(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?', cell.strip())
    if not m:
        return None
    d, mo = int(m.group(1)), int(m.group(2))
    if m.group(3):
        y = int(m.group(3))
        if y < 100:
            y += 2000
    else:
        today = date_cls.today()
        # Season logic: if today is Jan-Aug, Sep-Dec dates are from previous year
        if today.month <= 8 and mo >= 9:
            y = today.year - 1
        else:
            y = today.year
    try:
        return date_cls(y, mo, d)
    except ValueError:
        return None


def _find_empty_date_cols(rows: list, header: list) -> list[int]:
    """
    Return 0-based indices of date columns (col>=2) that are safe to delete:
    - Must have a valid date header
    - Must be in the PAST (not today, not future)
    - Must have no content in any body row
    """
    from datetime import date as _date
    today = _date.today()
    empty = []
    for c in range(2, len(header)):
        cell = header[c].strip() if c < len(header) else ""
        if not cell:
            continue
        d = _parse_date(cell)
        if d is None:
            continue
        # Never delete today or future columns
        if d >= today:
            continue
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


def design_tab(service, tab_name: str, sheet_id: int, delete_empty: bool = False) -> int:
    """
    Full design for one training-plan tab.
    - Highlights the last filled column in orange (most recent training)
    - Past/future columns get their standard colors
    - Never deletes columns
    Returns 0 (kept for API compatibility).
    """
    rows = _read_tab(service, tab_name)
    if not rows:
        return 0

    header = rows[0]
    deleted = 0

    n_cols = max(len(r) for r in rows) if rows else 3
    n_rows = len(rows)

    # ── Classify each date column as past / today / future ────────────────────
    today = date_cls.today()

    # ── Find last DATE column that has ANY content in body rows ───────────────
    # Only date-header columns are candidates — skip empty/non-date headers
    last_filled_col = None
    for c in range(n_cols - 1, 1, -1):
        if c >= len(header):
            continue
        cell = header[c].strip()
        if not cell or _parse_date(cell) is None:
            continue
        if any(c < len(row) and row[c].strip() for row in rows[1:]):
            last_filled_col = c
            break

    def _col_type(col_idx: int) -> str:
        """
        Return 'last', 'past', 'today', 'future', or 'nodate' for a date column.
        'last'   = most recently filled date column (orange even if past).
        'nodate' = column has no date header → treat as regular/neutral.
        """
        if col_idx >= len(header):
            return "nodate"
        cell = header[col_idx].strip()
        if not cell:
            return "nodate"
        d = _parse_date(cell)
        if d is None:
            return "nodate"
        # last_filled_col is always orange — regardless of past/future
        if col_idx == last_filled_col:
            return "last"
        if d == today:
            return "today"
        if d > today:
            return "future"
        return "past"

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
    # Column widths by type
    for c in range(2, n_cols):
        ctype = _col_type(c)
        if ctype in ("today", "last"):
            requests.append(_col_width(sheet_id, c, c + 1, 150))
        elif ctype == "future":
            requests.append(_col_width(sheet_id, c, c + 1, 135))
        # nodate → keep default 120px (already set by the bulk request above)

    # Row heights
    requests.append(_row_height(sheet_id, 0, n_rows, 34))

    # ── Header row (row 0): שעה + קבוצה = navy, dates by type ────────────────
    requests.append(_repeat_cell(sheet_id, 0, 1, 0, 2, {
        "backgroundColor": _NAVY,
        "textFormat": {"bold": True, "fontSize": 11, "foregroundColor": _WHITE},
        "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
        "wrapStrategy": "WRAP",
    }))
    for c in range(2, n_cols):
        ctype = _col_type(c)
        if ctype == "past":
            bg, txt, fsize = _PAST_HDR,   _WHITE, 10
        elif ctype in ("last", "today"):
            bg, txt, fsize = _TODAY_HDR,  _WHITE, 12
        elif ctype == "future":
            bg, txt, fsize = _FUTURE_HDR, _WHITE, 11
        else:  # nodate → same as past (blue), consistent with other training columns
            bg, txt, fsize = _PAST_HDR,   _WHITE, 10
        requests.append(_repeat_cell(sheet_id, 0, 1, c, c + 1, {
            "backgroundColor": bg,
            "textFormat": {"bold": True, "fontSize": fsize, "foregroundColor": txt},
            "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            "wrapStrategy": "WRAP",
        }))

    # ── Group blocks ───────────────────────────────────────────────────────────
    for idx, (g_start, g_end, _) in enumerate(group_blocks):
        g_color      = _GROUP_A      if idx % 2 == 0 else _GROUP_B
        g_color_last = _GROUP_LAST_A if idx % 2 == 0 else _GROUP_LAST_B

        # Group header row — split by column type so "last" cols get orange
        # First apply blue across all cols, then override last/today cols
        requests.append(_repeat_cell(sheet_id, g_start, g_start + 1, 0, n_cols, {
            "backgroundColor": g_color,
            "textFormat": {"bold": True, "fontSize": 10, "foregroundColor": _WHITE},
            "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            "wrapStrategy": "WRAP",
        }))
        for c in range(2, n_cols):
            ctype = _col_type(c)
            if ctype in ("last", "today"):
                requests.append(_repeat_cell(sheet_id, g_start, g_start + 1, c, c + 1, {
                    "backgroundColor": g_color_last,
                    "textFormat": {"bold": True, "fontSize": 10, "foregroundColor": _WHITE},
                    "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                    "wrapStrategy": "WRAP",
                }))

        # Content rows
        for r in range(g_start + 1, g_end):
            row_alt = (r - g_start) % 2 == 0
            # name cols (A, B) — always neutral
            row_bg = _ROW_A if row_alt else _ROW_B
            requests.append(_repeat_cell(sheet_id, r, r + 1, 0, 2, {
                "backgroundColor": row_bg,
                "textFormat": {"fontSize": 10},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                "wrapStrategy": "WRAP",
            }))
            # date content cols — color by type
            for c in range(2, n_cols):
                ctype = _col_type(c)
                if ctype == "past":
                    bg   = {"red": 0.86, "green": 0.91, "blue": 0.97} if row_alt else _PAST_CELL
                    bold = False
                elif ctype in ("today", "last"):
                    bg   = {"red": 1.00, "green": 0.98, "blue": 0.80} if row_alt else _TODAY_CELL
                    bold = True
                elif ctype == "future":
                    bg   = {"red": 1.00, "green": 0.99, "blue": 0.88} if row_alt else _FUTURE_CELL
                    bold = False
                else:  # nodate → same blue as past
                    bg   = {"red": 0.86, "green": 0.91, "blue": 0.97} if row_alt else _PAST_CELL
                    bold = False
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


def design_all_tabs() -> str:
    """Design all training plan tabs. Returns summary string."""
    import time
    service = _get_service()
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    tabs = [(s["properties"]["title"], s["properties"]["sheetId"]) for s in meta["sheets"]]

    results = []
    for tab_name, sid in tabs:
        try:
            design_tab(service, tab_name, sid)
            results.append(f"✅ {tab_name}")
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


def _norm_group(s: str) -> str:
    import re as _re
    s = s.strip()
    s = s.replace("–", "-").replace("—", "-").replace("–", "-").replace("—", "-")
    s = s.replace('"', "").replace("״", "").replace("'", "").replace("׳", "")
    s = _re.sub(r'\s*-\s*', '-', s)  # normalize spaces around dashes: "ז- בוגרים" → "ז-בוגרים"
    # Normalize Hebrew final-form letters to non-final for comparison (ן→נ, ם→מ, ף→פ, ך→כ, ץ→צ)
    # Enables "גנים" to match "גן חובה" (both start with גנ after normalization)
    s = s.translate(str.maketrans("ןםףךץ", "נמפכצ"))
    return s


def _group_matches(keyword: str, cell: str) -> bool:
    """Match group name: exact; substring if both >=3 chars;
    first-token prefix for 'גנ חובה'↔'גנימ-חמישי' (גן חובה ↔ גנים - חמישי)."""
    if keyword == cell:
        return True
    if len(keyword) >= 3 and len(cell) >= 3:
        if keyword in cell or cell in keyword:
            return True
    import re as _re
    def _first(s):
        return _re.split(r'[\s\-]+', s.strip())[0]
    fk, fc = _first(keyword), _first(cell)
    if len(fk) >= 2 and len(fc) >= 2:
        if fk == fc or fk.startswith(fc) or fc.startswith(fk):
            return True
    return False


def _find_group_rows_for_group(rows: list, group_keyword: str) -> list[int]:
    """Return 0-based row indices that belong to a group block matching keyword."""
    nk = _norm_group(group_keyword)
    block_start = None
    block_rows = []

    for i, row in enumerate(rows):
        if len(row) >= 2 and row[1].strip():
            if block_start is not None:
                cell = _norm_group(rows[block_start][1])
                if _group_matches(nk, cell):
                    return block_rows
            block_start = i
            block_rows = [i]
        elif block_start is not None:
            block_rows.append(i)

    if block_start is not None:
        cell = _norm_group(rows[block_start][1])
        if _group_matches(nk, cell):
            return block_rows
    return []


KEYWORDS_BY_BRANCH = {
    "default": {
        "חימום":  ["חימום", "ריצה", "גלגול", "שעון", "פתיחה", "גאורגי", "ג'ורג'י",
                   "warm", "שליחים", "ג'ונגל", "תופסת", "גה גה"],
        "תרגול":  ["תרגול", "הדגמה", "הסבר", "חזרות", "נושא", "כניסה", "טכניקה", "עבודה",
                   "מסלול", "הפלות", "strength", "bench", "pull", "squat", "deadlift"],
        "קרבות":  ["רנדורי", "קרבות", "קרב", "ספרינג", "מצבי", "ניקוד", "זהב",
                   "amrap", "emom", "metcon", "e2mom", "e3mom", "e1mom", "rope", "box jump"],
        "משחק":   ["משחק", "ציידים", "זאבים", "שועלים", "מלך", "כדור", "ביפ", "עיר",
                   "ג'ודופונג", "ביסט", "ישיבות", "קיר", "חיי שרה"],
        "כוח":    ["כוח", "טבאטה", "ברינג", "שכיבות", "מתח", "מקבילים", "פירמידה",
                   "tabata", "db lunge", "burpee"],
        "נוסף":   ["סיום", "שיחה", "דיון", "הערות", "תדריך", "תמונה"],
    },
    "איפון פייט": {
        "חימום":  ["שליחים", "ג'ונגל", "שוטרים", "ריצה", "אקרובטיקה", "חימום", "warm"],
        "תרגול":  ["טבאטה", "tabata", "כוח", "תרגיל", "סקוואט", "אולר", "עיירה",
                   "strength", "6 תרגילים", "בזוגות"],
        "קרבות":  ["ג'ודופונג", "קיר הנינג", "עיר הקרח", "ביסט גיימס", "מחניים",
                   "חיי שרה", "ציידים", "game", "תחרות"],
        "משחק":   ["זאבים", "ישיבות", "משחק", "שועלים", "אם נשאר"],
    },
    "פונקציונלי": {
        "חימום":  ["warm", "חימום", "ריצה", "גלגול", "תופס", "שליחים"],
        "תרגול":  ["strength", "bench", "pull", "squat", "deadlift", "press",
                   "e2mom", "e3mom", "weighted", "pistol"],
        "קרבות":  ["amrap", "emom", "e1mom", "metcon", "rope climb", "box jump",
                   "shuttle", "burpee", "lunge", "front squat"],
        "כוח":    ["מתיחות", "cooldown", "stretch", "שחרור"],
    },
}


def smart_map_items(items: list[str], n_rows: int, branch: str = "") -> list[str]:
    """
    Map plan items to sheet rows positionally: line 1 → row 1, line 2 → row 2, etc.
    Content is saved exactly as written. Extra items overflow into the last row.
    """
    if not items:
        return [""] * n_rows

    result = [""] * n_rows

    # Positional fill: item i → row i
    remaining = [item for item in items if item]
    for rt_idx in range(n_rows):
        if remaining:
            result[rt_idx] = remaining.pop(0)

    # Overflow: append extra items to last row so nothing is lost
    if remaining and n_rows > 0:
        last_idx = n_rows - 1
        overflow = "\n".join(remaining)
        result[last_idx] = (result[last_idx] + "\n" + overflow).strip() if result[last_idx] else overflow

    return result


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

    _log(f"    > קבוצה: {group} | טאב: {tab_name} | תא: {col_letter}")

    rows = _read_tab(service, tab_name)
    all_group_rows = _find_group_rows_for_group(rows, group)

    if not all_group_rows:
        raise ValueError(f"קבוצה '{group}' לא נמצאה בלשונית {tab_name}")

    content_rows = all_group_rows

    mapped = smart_map_items(plan_items, len(content_rows), branch=branch)

    updates = []
    for i, item in enumerate(mapped):
        if not item:
            continue
        row_1 = content_rows[i] + 1  # +1 = convert 0-indexed to sheet row number
        updates.append({
            "range": f"'{tab_name}'!{col_letter}{row_1}",
            "values": [[item]]
        })
        _log(f"      שורה {row_1}: {ROW_TYPES[i] if i < len(ROW_TYPES) else '?'} = {item[:30]}...")

    if updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"valueInputOption": "RAW", "data": updates}
        ).execute()
        _log(f"      ✓ {len(updates)} תאים עודכנו בגיליון")

    # Refresh design after save
    try:
        design_tab(service, tab_name, sheet_id, delete_empty=False)
        _log(f"      ✓ עיצוב רענן")
    except Exception as e:
        _log(f"      ⚠ עיצוב לא רענן: {e}")

    # Save to archive
    try:
        import training_archive as _arc
        content = {ROW_TYPES[i]: mapped[i] for i in range(len(mapped)) if i < len(ROW_TYPES) and mapped[i]}
        _arc.save_plan(branch, tab_name, group, plan_date.isoformat(), content)
        _log(f"      ✓ ארכיון שמור")
    except Exception as e:
        _log(f"      ⚠ ארכיון לא שמור: {e}")

    date_str = f"{plan_date.day}/{plan_date.month}"
    summary = f"✅ נשמר בגיליון {tab_name} — {group} — {date_str} ({len(updates)} שורות)"
    return summary


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


def detect_branch_and_date(text: str):
    """
    Try to detect branch name and date from free text.
    Returns (branch_or_None, date_or_None).
    """
    import re
    from datetime import date, timedelta

    # Detect branch — longest match wins
    branch = None
    for b in sorted(BRANCH_TABS, key=len, reverse=True):
        if b in text:
            branch = b
            break

    # Infer branch from group names if no explicit branch name found
    if not branch:
        import weekly_schedule as _ws
        from collections import Counter
        branch_counter: Counter = Counter()
        for day_scheds in _ws.SCHEDULE.values():
            for sched in day_scheds:
                b = sched["branch"]
                for g in sched["groups"]:
                    gn = g["name"]
                    # Match group name appearing as standalone line (with optional "כיתות" prefix)
                    if re.search(
                        r'(?m)^(?:כיתות?\s+)?' + re.escape(gn) + r'\s*$', text
                    ):
                        branch_counter[b] += 1
        if branch_counter:
            top_branch, top_count = branch_counter.most_common(1)[0]
            if top_count >= 2:
                branch = top_branch

    # Detect date — DD/MM or relative words
    plan_date = None
    today = date.today()
    if "היום" in text:
        plan_date = today
    elif "מחר" in text:
        plan_date = today + timedelta(days=1)
    else:
        m = re.search(r'(\d{1,2})[/.](\d{1,2})', text)
        if m:
            try:
                plan_date = date(today.year, int(m.group(2)), int(m.group(1)))
            except ValueError:
                pass

    return branch, plan_date


def preview_plan(branch: str, plan_date, plan_text: str) -> list[dict]:
    """
    Parse plan without saving. Returns list of:
      {"group": name, "time": time, "rows": [(row_type, value), ...]}
    Returns empty list if no groups found.
    """
    import weekly_schedule as _ws
    sched_groups = _ws.groups_for_branch_on_date(branch, plan_date)
    if not sched_groups:
        return []
    sections = _split_plan_into_sections(plan_text, sched_groups)
    tab_name = BRANCH_TABS.get(branch)
    result = []
    for group_info, items in sections:
        n_rows = None
        if tab_name:
            try:
                svc = _get_service()
                rows = _read_tab(svc, tab_name)
                grp_rows = _find_group_rows_for_group(rows, group_info["name"])
                n_rows = len(grp_rows) if grp_rows else None
            except Exception:
                pass
        if n_rows is None:
            n_rows = 4 if branch in ("איפון פייט",) else (3 if branch == "פונקציונלי" else 6)
        mapped = smart_map_items(items, n_rows, branch=branch)
        row_types = ROW_TYPES[:n_rows]
        rows_preview = [(rt, val) for rt, val in zip(row_types, mapped) if val]
        result.append({
            "group": group_info["name"],
            "time":  group_info.get("time", ""),
            "rows":  rows_preview,
        })
    return result


def verify_plan_saved(branch: str, plan_date, preview: list[dict]) -> list[dict]:
    """
    Read back from sheet what was actually saved. Returns list of:
      {"group": name, "ok": bool, "written": [(row_type, value), ...]}
    """
    import weekly_schedule as _ws
    tab_name = BRANCH_TABS.get(branch)
    if not tab_name:
        return []
    try:
        svc = _get_service()
        col_0 = _find_or_create_date_col(svc, tab_name, plan_date)
        rows = _read_tab(svc, tab_name)
        verify_results = []
        for g in preview:
            grp_rows = _find_group_rows_for_group(rows, g["group"])
            content_grp_rows = grp_rows if grp_rows else []
            n_rows = len(content_grp_rows)
            row_types = ROW_TYPES[:n_rows]
            written = []
            for i, rt in enumerate(row_types):
                row_idx = content_grp_rows[i] if i < len(content_grp_rows) else -1
                val = ""
                if row_idx >= 0 and row_idx < len(rows):
                    row = rows[row_idx]
                    val = row[col_0] if col_0 < len(row) else ""
                if val:
                    written.append((rt, val))
            expected_count = len(g["rows"])
            ok = len(written) >= expected_count and expected_count > 0
            verify_results.append({"group": g["group"], "ok": ok, "written": written})
        return verify_results
    except Exception:
        return []


def save_full_day(branch: str, plan_date, plan_text: str) -> str:
    """
    Save a full training day plan for a branch.
    Automatically finds all groups for that branch+day from the schedule,
    splits the plan_text among them, and saves each to its correct sheet block.
    Returns a summary string with full logging.
    """
    import weekly_schedule as _ws
    from datetime import date as _date_cls

    # Convert to date_cls if needed
    if not isinstance(plan_date, _date_cls):
        if isinstance(plan_date, str):
            plan_date = _date_cls.fromisoformat(plan_date)

    _log(f"🟢 START: save_full_day({branch}, {plan_date})")

    # Validate branch
    if branch not in BRANCH_TABS:
        err = f"❌ סניף לא מוכר: {branch}"
        _log(err)
        raise ValueError(err)

    # Get groups for this branch on this day (from schedule)
    sched_groups = _ws.groups_for_branch_on_date(branch, plan_date)
    if not sched_groups:
        warn = f"⚠️ לא מוגדרות קבוצות ל-{branch} ביום {plan_date.strftime('%A %d/%m')}"
        _log(warn)
        return warn

    _log(f"  קבוצות בתוכנית: {', '.join([g['name'] for g in sched_groups])}")

    # Validate that all groups in schedule are valid (not a day they shouldn't be)
    # (This check is informational — the schedule already defines what's valid)

    # Parse the plan text into sections, one per group
    sections = _split_plan_into_sections(plan_text, sched_groups)

    service = _get_service()
    tab_name = BRANCH_TABS.get(branch)
    sheet_id = _get_sheet_id(service, tab_name)

    _log(f"  טאב בגיליון: '{tab_name}' (ID: {sheet_id})")

    # If the plan only labeled ONE group, spread its content to all other groups too.
    groups_with_content = [items for _, items in sections if items]
    fallback_items = groups_with_content[0] if len(groups_with_content) == 1 else []

    results = []
    saved_any = False

    for group_info, items in sections:
        group_name = group_info["name"]

        # Cancelled group → write "בוטל" to sheet instead of content
        if group_info.get("cancelled"):
            try:
                save_plan_to_sheet(branch, group_name, plan_date, ["בוטל"])
                results.append(f"🚫 {group_name}: בוטל")
                _log(f"  ✓ שמרתי: {group_name} = בוטל")
                saved_any = True
            except Exception as e:
                results.append(f"⚠️ {group_name}: לא נכתב (בוטל) — {e}")
                _log(f"  ✗ שגיאה בשמירה {group_name}: {e}")
            continue

        effective_items = items if items else fallback_items
        if not effective_items:
            results.append(f"⚠️ {group_name}: אין תוכן")
            _log(f"  ⚠ {group_name}: אין תוכן")
            continue

        try:
            save_plan_to_sheet(branch, group_name, plan_date, effective_items)
            results.append(f"✅ {group_name}")
            _log(f"  ✓ שמרתי: {group_name} ({len(effective_items)} שורות)")
            saved_any = True
        except ValueError as e:
            results.append(f"⚠️ {group_name}: {e}")
            _log(f"  ✗ {group_name}: {e}")
        except Exception as e:
            results.append(f"❌ {group_name}: {e}")
            _log(f"  ❌ {group_name}: {type(e).__name__}: {e}")

    summary = "\n".join(results)
    if saved_any:
        _log(f"🟢 SUCCESS: שמרתי תוכנית לגיליון")
    else:
        _log(f"🔴 PARTIAL: לא הצליח לשמור שום קבוצה")
    _log("=" * 60)
    return summary


def clear_plan_from_sheet(branch: str, plan_date) -> str:
    """
    Clear all training plan content for a given branch+date from the sheet.
    Writes empty strings to all group/content cells in that date column.
    Returns a summary string.
    """
    tab_name = BRANCH_TABS.get(branch)
    if not tab_name:
        raise ValueError(f"סניף לא מוכר: {branch}")

    service = _get_service()
    sheet_id = _get_sheet_id(service, tab_name)
    # Check if date column exists (don't create it)
    rows_check = _read_tab(service, tab_name)
    date_str = f"{plan_date.day}/{plan_date.month}"
    header = rows_check[0] if rows_check else []
    col_0 = next((i for i, c in enumerate(header) if c.strip() == date_str), None)
    if col_0 is None:
        return f"⚠️ לא נמצא תאריך {plan_date.day}/{plan_date.month} בגיליון {tab_name}"
    col_letter = _col_letter(col_0)

    rows = _read_tab(service, tab_name)
    # Find all content rows (skip header row 0)
    updates = []
    for i, row in enumerate(rows):
        if i == 0:
            continue
        # Skip rows that are group-name rows (col A or B has group name, col date empty is fine)
        if col_0 < len(row) and row[col_0]:
            updates.append({
                "range": f"'{tab_name}'!{col_letter}{i+1}",
                "values": [[""]]
            })

    if updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"valueInputOption": "RAW", "data": updates}
        ).execute()
        return f"✅ נמחקו {len(updates)} תאים בגיליון {tab_name} לתאריך {plan_date.day}/{plan_date.month}"
    else:
        return f"⚠️ לא נמצא תוכן לתאריך {plan_date.day}/{plan_date.month} בגיליון {tab_name}"


def load_plan_from_sheet(branch: str, plan_date) -> dict:
    """
    Read the current training plan from the sheet for a given branch+date.
    Returns {group_name: {row_type: value, ...}} or empty dict if not found.
    """
    tab_name = BRANCH_TABS.get(branch)
    if not tab_name:
        return {}

    service = _get_service()
    rows = _read_tab(service, tab_name)
    if not rows:
        return {}

    date_str = f"{plan_date.day}/{plan_date.month}"
    header = rows[0]
    col_idx = next((i for i, c in enumerate(header) if str(c).strip() == date_str), None)
    if col_idx is None:
        return {}

    # Read group rows — skip header (row 0) same as every other _find_group_rows call site
    group_rows = _find_group_rows(rows[1:])  # indices are relative to rows[1:]
    result = {}
    for start_row, end_row, group_name in group_rows:
        items = {}
        for r_idx in range(start_row, end_row):  # end_row is exclusive
            row = rows[r_idx + 1] if r_idx + 1 < len(rows) else []  # +1 for skipped header
            row_type = row[1].strip() if len(row) > 1 else ""
            val = row[col_idx].strip() if col_idx < len(row) else ""
            if row_type and val:
                items[row_type] = val
        if items:
            result[group_name] = items
    return result


ROW_TYPE_LABELS = ["חימום", "תרגול", "קרבות", "משחק", "כוח", "נוסף", "סיום",
                   "warm", "strength", "metcon", "cooldown"]


def _split_plan_into_sections(text: str, sched_groups: list) -> list:
    """
    Split plan text into per-group sections.
    Returns list of (group_info_dict, items_list).

    Supports formats (tried in order):
      1. "ב-ד:\nחימום: ...\nתרגול: ..."  (group name colon + labeled rows)
      2. "⏰ 14:30–15:30 | 👥 ב-ד"  (Claude emoji, handles em-dash)
      3. "**ב-ד** (14:30)" or "### ב-ד"  (Markdown headers)
      4. No markers → all content goes to first group only
    """
    import re

    # Normalize em-dash and similar to regular dash for matching
    text = text.replace('–', '-').replace('—', '-').replace('–', '-').replace('—', '-')

    ROW_TYPE_PREFIXES = tuple(r + ":" for r in ROW_TYPE_LABELS)

    def _clean_line(line: str) -> str:
        line = line.strip()
        line = re.sub(r'^[•\-\*]\s*', '', line)
        if line.startswith("נושא:"):
            line = line.replace("נושא:", "").strip()
        return line

    def _extract_items(block: str) -> list:
        items = []
        for line in block.splitlines():
            cleaned = _clean_line(line)
            if not cleaned or re.match(r'^[-=]{3,}$', cleaned):
                continue
            # Strip leading row-type label ("חימום: content" → "content")
            for prefix in ROW_TYPE_PREFIXES:
                if cleaned.lower().startswith(prefix.lower()):
                    cleaned = cleaned[len(prefix):].strip()
                    break
            if cleaned:
                items.append(cleaned)
        return items

    def _best_match(label: str, groups: list, used: set) -> int:
        label_clean = re.sub(r'[*_#\(\)0-9:.\s]', '', label).strip()
        # Also normalize em-dash in label just in case
        label_clean = label_clean.replace('–', '-').replace('—', '-')
        best_idx, best_score = -1, 0
        for i, sg in enumerate(groups):
            if i in used:
                continue
            name = sg["name"]
            name_clean = re.sub(r'\s+', '', name)
            label_nospace = re.sub(r'\s+', '', label_clean)
            score = 0
            if label_nospace == name_clean:
                score = 10
            elif label_nospace in name_clean or name_clean in label_nospace:
                score = 6
            elif any(part and part in name for part in re.split(r'[-–]', label_clean) if part):
                score = 3
            if score > best_score:
                best_score, best_idx = score, i
        return best_idx if best_score > 0 else -1

    # ── Format 1: group name colon "ב-ד:" or "ב-ד: content" ──
    group_names_pattern = '|'.join(re.escape(sg["name"]) for sg in sched_groups)
    colon_splits = list(re.finditer(
        rf'(?m)^[ \t]*({group_names_pattern})\s*[:()\d\-\s]*:\s*(.*)?$',
        text
    ))
    # Also try simpler: line is just the group name + colon
    if not colon_splits:
        colon_splits = list(re.finditer(
            rf'(?m)^[ \t]*({group_names_pattern})\s*:\s*(.*)?$',
            text
        ))

    if colon_splits:
        sections_raw = []
        for i, m in enumerate(colon_splits):
            label = m.group(1).strip()
            inline_content = m.group(2).strip() if m.group(2) else ""
            start = m.end()
            end = colon_splits[i + 1].start() if i + 1 < len(colon_splits) else len(text)
            block_text = (inline_content + "\n" + text[start:end]).strip()
            items = _extract_items(block_text)
            sections_raw.append((label, items))

        result = []
        used = set()
        for label, items in sections_raw:
            idx = _best_match(label, sched_groups, used)
            if idx >= 0:
                used.add(idx)
                result.append((sched_groups[idx], items))
        for i, sg in enumerate(sched_groups):
            if i not in used:
                result.append((sg, []))
        return result

    # ── Format 2: emoji format ⏰ TIME | 👥 GROUP (handles em-dash) ──
    emoji_splits = list(re.finditer(
        r'(?m)^(?:⏰\s*)?(\d{1,2}:\d{2}[^|\n]*)\s*\|\s*(?:👥\s*)?(.{1,30}?)(?:\s*\(.*?)?\s*$',
        text
    ))

    if emoji_splits:
        sections_raw = []
        for i, m in enumerate(emoji_splits):
            label = m.group(2).strip().rstrip('*').strip()
            start = m.end()
            end = emoji_splits[i + 1].start() if i + 1 < len(emoji_splits) else len(text)
            items = _extract_items(text[start:end])
            sections_raw.append((label, items))

        result = []
        used = set()
        for label, items in sections_raw:
            idx = _best_match(label, sched_groups, used)
            if idx >= 0:
                used.add(idx)
                result.append((sched_groups[idx], items))
        for i, sg in enumerate(sched_groups):
            if i not in used:
                result.append((sg, []))
        return result

    # ── Format 3: Markdown headers **ב-ד** or ### ב-ד ──
    md_splits = list(re.finditer(
        r'(?m)^(?:#{1,3}\s*|\*{1,2})(' + group_names_pattern + r')\*{0,2}.*$',
        text
    ))

    if md_splits:
        sections_raw = []
        for i, m in enumerate(md_splits):
            label = m.group(1).strip()
            start = m.end()
            end = md_splits[i + 1].start() if i + 1 < len(md_splits) else len(text)
            items = _extract_items(text[start:end])
            sections_raw.append((label, items))

        result = []
        used = set()
        for label, items in sections_raw:
            idx = _best_match(label, sched_groups, used)
            if idx >= 0:
                used.add(idx)
                result.append((sched_groups[idx], items))
        for i, sg in enumerate(sched_groups):
            if i not in used:
                result.append((sg, []))
        return result

    # ── Format 5: plain group-name header line (no colon, no emoji) ──
    # User writes: "כיתות ד-ו\nחימום...", "ז-בוגרים\nתרגול...", "גנים\nאין אימון"
    def _is_group_line(ln: str) -> int:
        """Return group idx if this line is just a group name, else -1."""
        clean = re.sub(r'^כיתות?\s+', '', ln.strip()).strip()
        clean = re.sub(r'^[*#\s]+|[*#\s]+$', '', clean)
        if not clean or len(clean) > 25:
            return -1
        nc = _norm_group(clean)
        for i, sg in enumerate(sched_groups):
            ng = _norm_group(sg["name"])
            if nc == ng:
                return i
            if len(nc) >= 2 and len(ng) >= 2 and (nc in ng or ng in nc):
                return i
            # Fuzzy first-word prefix: handles גנים↔גן חובה, ז-בוגרים↔ז-בוגרים
            fw_nc = nc.split()[0] if ' ' in nc else nc
            fw_ng = ng.split()[0] if ' ' in ng else ng
            if len(fw_nc) >= 2 and len(fw_ng) >= 2 and fw_nc[:2] == fw_ng[:2] and (
                fw_nc in fw_ng or fw_ng in fw_nc or fw_nc.startswith(fw_ng) or fw_ng.startswith(fw_nc)
            ):
                return i
        return -1

    txt_lines = text.splitlines()
    f5_headers = [(i, _is_group_line(ln.strip()))
                  for i, ln in enumerate(txt_lines)
                  if _is_group_line(ln.strip()) >= 0]

    if f5_headers:
        _SKIP_ITEMS = {"אין אימון", "לא מתאמנים", "ביטול", "אין", ""}
        sections_raw = []
        for k, (li, gi) in enumerate(f5_headers):
            end_li = f5_headers[k + 1][0] if k + 1 < len(f5_headers) else len(txt_lines)
            block = "\n".join(txt_lines[li + 1:end_li])
            items = [it for it in _extract_items(block) if it.strip() not in _SKIP_ITEMS]
            sections_raw.append((gi, items))

        result = []
        used_gi = {gi for gi, _ in sections_raw}
        for gi, items in sorted(sections_raw, key=lambda x: x[0]):
            result.append((sched_groups[gi], items))
        for i, sg in enumerate(sched_groups):
            if i not in used_gi:
                result.append((sg, []))
        return result

    # ── Format 4: No markers — all content to first group ──
    all_items = _extract_items(text)
    result = [(sched_groups[0], all_items)] if sched_groups else []
    for sg in sched_groups[1:]:
        result.append((sg, []))
    return result


def is_multigroup_plan(text: str) -> bool:
    """Returns True if text looks like a multi-group training plan."""
    import re
    time_count = len(re.findall(r'⏰|👥|\d{1,2}:\d{2}\s*\|', text))
    bullet_count = len(re.findall(r'^[•\-]', text, re.MULTILINE))
    return time_count >= 2 and bullet_count >= 3
