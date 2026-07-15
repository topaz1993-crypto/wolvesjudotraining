"""מחנה קיץ — ניהול גיליון רשומים."""

import os, pickle, base64
from datetime import date, timedelta
import googleapiclient.discovery

SPREADSHEET_ID = '1lDULmVEYkbbASAdG2MKiozoV1gzsYQ_P-sw_CyilhyE'
SHEET_NAME = 'רשומים'

GRADE_ORDER = {'גן':0,'א':1,'ב':2,'ג':3,'ד':4,'ה':5,'ו':6,'ז':7,'ח':8,'ט':9,'י':10,'יא':11,'יב':12}

BRANCH_BG = {
    'סירקין':    {'red': 0.67, 'green': 0.84, 'blue': 0.90},
    'חגור':      {'red': 0.72, 'green': 0.88, 'blue': 0.71},
    'נווה ירק':  {'red': 1.00, 'green': 0.87, 'blue': 0.60},
    'אהרונוביץ': {'red': 0.87, 'green': 0.75, 'blue': 0.94},
}
BRANCH_TEXT = {
    'סירקין':    {'red': 0.07, 'green': 0.33, 'blue': 0.53},
    'חגור':      {'red': 0.14, 'green': 0.42, 'blue': 0.13},
    'נווה ירק':  {'red': 0.49, 'green': 0.27, 'blue': 0.04},
    'אהרונוביץ': {'red': 0.30, 'green': 0.08, 'blue': 0.47},
}
HEADER_BG  = {'red': 0.18, 'green': 0.22, 'blue': 0.38}
HEADER_FG  = {'red': 1.0,  'green': 1.0,  'blue': 1.0}
DEFAULT_BG = {'red': 0.95, 'green': 0.95, 'blue': 0.95}

COLS = ['שם', 'כיתה', 'סניף', 'מידת חולצה', 'הערות', 'תשלום', 'צהרון']

CAMP_START   = date(2026, 7, 22)
CAMP_END     = date(2026, 8, 4)
CAMP_DAYS_WD = {0, 1, 2, 3, 6}   # Sun-Thu (Israeli work week; Mon=0..Sat=5,Sun=6)
ATT_TAB      = "נוכחות מחנה"
SHIRTS_TAB   = "הזמנת חולצות"

_GREEN = {'red': 0.20, 'green': 0.78, 'blue': 0.35}
_RED   = {'red': 0.90, 'green': 0.27, 'blue': 0.27}
_WHITE = {'red': 1.0,  'green': 1.0,  'blue': 1.0}


def _get_service():
    b64 = os.environ.get('GOOGLE_CREDS_B64')
    if b64:
        creds = pickle.loads(base64.b64decode(b64 + "=="))
    else:
        with open(os.path.expanduser('~/.wolves_judo_token.pickle'), 'rb') as f:
            creds = pickle.load(f)
    return googleapiclient.discovery.build('sheets', 'v4', credentials=creds)


def get_students():
    service = _get_service()
    rows = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f'{SHEET_NAME}!A1:G80'
    ).execute().get('values', [])
    result = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        result.append({
            'name':   row[0].strip() if len(row) > 0 else '',
            'grade':  row[1].strip() if len(row) > 1 else '',
            'branch': row[2].strip() if len(row) > 2 else '',
            'shirt':  row[3].strip() if len(row) > 3 else '',
            'notes':  row[4].strip() if len(row) > 4 else '',
            'paid':   row[5].strip() if len(row) > 5 else '',
            'lunch':  row[6].strip() if len(row) > 6 else '',
        })
    return result


def get_stats():
    students = get_students()
    by_week = {}
    by_branch = {}
    paid = 0
    lunch = 0
    for s in students:
        w = s['notes'] or 'לא ידוע'
        by_week[w] = by_week.get(w, 0) + 1
        b = s['branch'] or 'לא ידוע'
        by_branch[b] = by_branch.get(b, 0) + 1
        if s['paid']:
            paid += 1
        if s['lunch']:
            lunch += 1
    return {
        'total': len(students),
        'by_week': by_week,
        'by_branch': by_branch,
        'paid': paid,
        'lunch': lunch,
    }


def add_student(name, grade, branch, week='שבועיים'):
    service = _get_service()
    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f'{SHEET_NAME}!A:G',
        valueInputOption='RAW',
        insertDataOption='INSERT_ROWS',
        body={'values': [[name, grade, branch, '', week, '', '']]}
    ).execute()
    format_sheet()


def update_student(name, field, value):
    """מעדכן שדה. field: 'grade'/'branch'/'shirt'/'notes'/'paid'/'lunch'"""
    col_map = {'name':0, 'grade':1, 'branch':2, 'shirt':3, 'notes':4, 'paid':5, 'lunch':6}
    col_idx = col_map.get(field)
    if col_idx is None:
        raise ValueError(f'שדה לא מוכר: {field}')
    service = _get_service()
    rows = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f'{SHEET_NAME}!A1:G80'
    ).execute().get('values', [])
    for i, row in enumerate(rows[1:], 2):
        if row and row[0].strip() == name:
            col_letter = 'ABCDEFG'[col_idx]
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f'{SHEET_NAME}!{col_letter}{i}',
                valueInputOption='RAW',
                body={'values': [[value]]}
            ).execute()
            return True
    return False


def delete_student(name):
    """מוחק רשום לפי שם."""
    service = _get_service()
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheet_id = next(s['properties']['sheetId'] for s in meta['sheets']
                    if s['properties']['title'] == SHEET_NAME)

    rows = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f'{SHEET_NAME}!A1:G80'
    ).execute().get('values', [])

    for i, row in enumerate(rows[1:], 1):  # 0-indexed (body rows start at 1), skip header
        if row and row[0].strip() == name:
            service.spreadsheets().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={
                    'requests': [{
                        'deleteDimension': {
                            'range': {
                                'sheetId': sheet_id,
                                'dimension': 'ROWS',
                                'startIndex': i,
                                'endIndex': i + 1
                            }
                        }
                    }]
                }
            ).execute()
            return True
    return False


def format_sheet():
    service = _get_service()
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheet_id = next(s['properties']['sheetId'] for s in meta['sheets']
                    if s['properties']['title'] == SHEET_NAME)

    students = get_students()
    students.sort(key=lambda r: (GRADE_ORDER.get(r['grade'], 99), r['name']))

    new_values = [COLS] + [
        [s['name'], s['grade'], s['branch'], s['shirt'], s['notes'], s['paid'], s['lunch']]
        for s in students
    ]
    service.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID, range=f'{SHEET_NAME}!A1:G80'
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID, range=f'{SHEET_NAME}!A1',
        valueInputOption='RAW', body={'values': new_values}
    ).execute()

    num_rows = len(students) + 1
    req = []

    req.append({'repeatCell': {
        'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': 80,
                  'startColumnIndex': 0, 'endColumnIndex': 7},
        'cell': {'userEnteredFormat': {}}, 'fields': 'userEnteredFormat',
    }})
    req.append({'repeatCell': {
        'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': 1,
                  'startColumnIndex': 0, 'endColumnIndex': 7},
        'cell': {'userEnteredFormat': {
            'backgroundColor': HEADER_BG,
            'textFormat': {'foregroundColor': HEADER_FG, 'bold': True, 'fontSize': 12},
            'horizontalAlignment': 'CENTER', 'verticalAlignment': 'MIDDLE',
        }},
        'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)',
    }})

    for i, s in enumerate(students):
        bg = BRANCH_BG.get(s['branch'], DEFAULT_BG)
        tc = BRANCH_TEXT.get(s['branch'], {'red': 0.1, 'green': 0.1, 'blue': 0.1})
        req.append({'repeatCell': {
            'range': {'sheetId': sheet_id, 'startRowIndex': i+1, 'endRowIndex': i+2,
                      'startColumnIndex': 0, 'endColumnIndex': 7},
            'cell': {'userEnteredFormat': {
                'backgroundColor': bg,
                'textFormat': {'foregroundColor': tc, 'fontSize': 11},
                'horizontalAlignment': 'CENTER', 'verticalAlignment': 'MIDDLE',
            }},
            'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)',
        }})

    req.append({'repeatCell': {
        'range': {'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': num_rows,
                  'startColumnIndex': 0, 'endColumnIndex': 1},
        'cell': {'userEnteredFormat': {
            'horizontalAlignment': 'RIGHT',
            'textFormat': {'bold': True, 'fontSize': 11},
        }},
        'fields': 'userEnteredFormat(horizontalAlignment,textFormat)',
    }})

    for col, px in enumerate([200, 55, 100, 70, 110, 70, 70]):
        req.append({'updateDimensionProperties': {
            'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS',
                      'startIndex': col, 'endIndex': col+1},
            'properties': {'pixelSize': px}, 'fields': 'pixelSize',
        }})

    req.append({'updateDimensionProperties': {
        'range': {'sheetId': sheet_id, 'dimension': 'ROWS',
                  'startIndex': 0, 'endIndex': num_rows},
        'properties': {'pixelSize': 36}, 'fields': 'pixelSize',
    }})

    thin  = {'style': 'SOLID', 'width': 1, 'color': {'red': 0.7, 'green': 0.7, 'blue': 0.7}}
    thick = {'style': 'SOLID_MEDIUM', 'width': 2, 'color': {'red': 0.3, 'green': 0.3, 'blue': 0.3}}
    req.append({'updateBorders': {
        'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': num_rows,
                  'startColumnIndex': 0, 'endColumnIndex': 7},
        'innerHorizontal': thin, 'innerVertical': thin,
        'top': thick, 'bottom': thick, 'left': thick, 'right': thick,
    }})

    req.append({'updateSheetProperties': {
        'properties': {'sheetId': sheet_id,
                       'gridProperties': {'frozenRowCount': 1}, 'rightToLeft': True},
        'fields': 'gridProperties.frozenRowCount,rightToLeft',
    }})

    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID, body={'requests': req}
    ).execute()


# ── Attendance tab ─────────────────────────────────────────────────────────────

def get_camp_days() -> list:
    """Return the 10 valid camp days (Sun-Thu, 22/7–4/8/2026)."""
    days, d = [], CAMP_START
    while d <= CAMP_END:
        if d.weekday() in CAMP_DAYS_WD:
            days.append(d)
        d += timedelta(days=1)
    return days


def _get_or_create_att_tab(service):
    """Return sheetId of ATT_TAB, creating it (RTL) if absent."""
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    for s in meta['sheets']:
        if s['properties']['title'] == ATT_TAB:
            return s['properties']['sheetId']
    resp = service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={'requests': [{'addSheet': {'properties': {'title': ATT_TAB, 'rightToLeft': True}}}]}
    ).execute()
    return resp['replies'][0]['addSheet']['properties']['sheetId']


def setup_attendance_tab():
    """Create the 'נוכחות מחנה' tab if it doesn't already have student rows.
    Idempotent — safe to call every time the screen is opened."""
    service = _get_service()
    sid = _get_or_create_att_tab(service)

    # If the tab already has student rows, don't overwrite (preserves marks)
    existing = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f'{ATT_TAB}!A2:A2'
    ).execute().get('values', [])
    if existing:
        return None  # already set up

    camp_days = get_camp_days()
    students = sorted(get_students(), key=lambda r: (GRADE_ORDER.get(r['grade'], 99), r['name']))

    n_cols = len(camp_days) + 1          # A=שם, B-K=dates
    col_end = chr(ord('A') + n_cols - 1)

    header = ['שם'] + [f"{d.day}/{d.month}" for d in camp_days]
    rows   = [header] + [[s['name']] + [''] * len(camp_days) for s in students]

    service.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID, range=f'{ATT_TAB}!A1:{col_end}60'
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID, range=f'{ATT_TAB}!A1',
        valueInputOption='RAW', body={'values': rows}
    ).execute()

    num_rows = len(students) + 1
    req = [
        {'repeatCell': {
            'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 60,
                      'startColumnIndex': 0, 'endColumnIndex': n_cols},
            'cell': {'userEnteredFormat': {}}, 'fields': 'userEnteredFormat',
        }},
        {'repeatCell': {
            'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 1,
                      'startColumnIndex': 0, 'endColumnIndex': n_cols},
            'cell': {'userEnteredFormat': {
                'backgroundColor': HEADER_BG,
                'textFormat': {'foregroundColor': HEADER_FG, 'bold': True, 'fontSize': 11},
                'horizontalAlignment': 'CENTER', 'verticalAlignment': 'MIDDLE',
            }},
            'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)',
        }},
        {'repeatCell': {
            'range': {'sheetId': sid, 'startRowIndex': 1, 'endRowIndex': num_rows,
                      'startColumnIndex': 0, 'endColumnIndex': 1},
            'cell': {'userEnteredFormat': {
                'horizontalAlignment': 'RIGHT',
                'textFormat': {'bold': True, 'fontSize': 11},
            }},
            'fields': 'userEnteredFormat(horizontalAlignment,textFormat)',
        }},
        {'updateDimensionProperties': {
            'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': 0, 'endIndex': 1},
            'properties': {'pixelSize': 180}, 'fields': 'pixelSize',
        }},
        {'updateDimensionProperties': {
            'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': 1, 'endIndex': n_cols},
            'properties': {'pixelSize': 55}, 'fields': 'pixelSize',
        }},
        {'updateDimensionProperties': {
            'range': {'sheetId': sid, 'dimension': 'ROWS', 'startIndex': 0, 'endIndex': num_rows},
            'properties': {'pixelSize': 36}, 'fields': 'pixelSize',
        }},
        {'updateSheetProperties': {
            'properties': {'sheetId': sid, 'rightToLeft': True,
                           'gridProperties': {'frozenRowCount': 1, 'frozenColumnCount': 1}},
            'fields': 'rightToLeft,gridProperties.frozenRowCount,gridProperties.frozenColumnCount',
        }},
    ]
    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID, body={'requests': req}
    ).execute()
    return len(students)


def _day_col_index(day: date) -> int:
    """0-based column index for a camp day (0 = name column A, 1 = first date B, …)."""
    for i, d in enumerate(get_camp_days()):
        if d == day:
            return i + 1
    return -1


def get_attendance(day: date) -> dict:
    """Return {name: 'V'/'X'/''} for all students on a camp day."""
    service = _get_service()
    try:
        rows = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range=f'{ATT_TAB}!A1:K60'
        ).execute().get('values', [])
    except Exception:
        return {}
    col_idx = _day_col_index(day)
    if col_idx < 0 or not rows:
        return {}
    result = {}
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        name = row[0].strip()
        val  = row[col_idx].strip() if len(row) > col_idx else ''
        result[name] = val
    return result


def save_attendance_batch(day: date, marks: dict):
    """Write all attendance marks for one day in 2 API calls (values + colors)."""
    service = _get_service()
    sid = _get_or_create_att_tab(service)
    col_idx = _day_col_index(day)
    if col_idx < 0:
        raise ValueError(f"לא יום מחנה: {day}")
    col_letter = chr(ord('A') + col_idx)

    name_rows = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f'{ATT_TAB}!A1:A60'
    ).execute().get('values', [])

    col_values = []
    color_reqs = []
    for i, row in enumerate(name_rows[1:]):
        name = row[0].strip() if row else ''
        if not name:
            break
        status = marks.get(name, '')
        col_values.append([status])
        bg = _GREEN if status == 'V' else (_RED if status == 'X' else _WHITE)
        color_reqs.append({'repeatCell': {
            'range': {'sheetId': sid,
                      'startRowIndex': i + 1, 'endRowIndex': i + 2,
                      'startColumnIndex': col_idx, 'endColumnIndex': col_idx + 1},
            'cell': {'userEnteredFormat': {'backgroundColor': bg}},
            'fields': 'userEnteredFormat.backgroundColor',
        }})

    if col_values:
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f'{ATT_TAB}!{col_letter}2',
            valueInputOption='RAW', body={'values': col_values}
        ).execute()
    if color_reqs:
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID, body={'requests': color_reqs}
        ).execute()


# ── Shirt distribution tab ─────────────────────────────────────────────────────

def get_shirt_status() -> list:
    """Return [{name, size, received}] from the 'הזמנת חולצות' tab.
    Detects size column per section (kids: col D; instructors: col B)."""
    service = _get_service()
    rows = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f'{SHIRTS_TAB}!A1:E80'
    ).execute().get('values', [])
    result = []
    size_col = None
    for row in rows:
        if not row:
            continue
        name = row[0].strip()
        if not name:
            continue
        if 'סיכום' in name:   # stop at summary section
            break
        if name in ('👦 ילדים', '👕 מדריכים'):
            continue
        if name == 'שם':      # column header — detect size column
            cols = [c.strip() for c in row]
            size_col = cols.index('מידה') if 'מידה' in cols else None
            continue
        if name in {'סה"כ', 'כמות', 'מידה', ''} or size_col is None:
            continue
        size = row[size_col].strip() if len(row) > size_col else ''
        if not size:
            continue
        received = row[4].strip() if len(row) > 4 else ''
        result.append({'name': name, 'size': size, 'received': received})
    return result


def add_instructor_shirt(name: str, size: str) -> bool:
    """הוסף חולצה למדריך בסעיף מדריכים."""
    service = _get_service()
    rows = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f'{SHIRTS_TAB}!A1:E80'
    ).execute().get('values', [])

    # Find "👕 מדריכים" section and get size column
    instructor_start = None
    size_col = None
    for i, row in enumerate(rows):
        if row and '👕 מדריכים' in row[0]:
            instructor_start = i + 1  # next row is header
            if instructor_start < len(rows):
                header_row = rows[instructor_start]
                cols = [c.strip() for c in header_row] if header_row else []
                size_col = cols.index('מידה') if 'מידה' in cols else None
            break

    if instructor_start is None or size_col is None:
        return False

    # Find first empty row after header in instructor section
    for i in range(instructor_start + 1, len(rows)):
        if not rows[i] or (rows[i] and not rows[i][0].strip()):
            # Insert here
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f'{SHIRTS_TAB}!A{i + 1}:E{i + 1}',
                valueInputOption='RAW',
                body={'values': [[name, '', '', size, '']]}
            ).execute()
            return True
    return False


def mark_shirt_received(name: str, value: str = '✓') -> bool:
    """Write value to col E of 'הזמנת חולצות' for the given person."""
    service = _get_service()
    rows = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f'{SHIRTS_TAB}!A1:E80'
    ).execute().get('values', [])
    for i, row in enumerate(rows):
        if row and row[0].strip() == name:
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f'{SHIRTS_TAB}!E{i + 1}',
                valueInputOption='RAW', body={'values': [[value]]}
            ).execute()
            return True
    return False
