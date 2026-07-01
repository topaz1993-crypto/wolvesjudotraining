"""מחנה קיץ — ניהול גיליון רשומים."""

import os, pickle, base64
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
