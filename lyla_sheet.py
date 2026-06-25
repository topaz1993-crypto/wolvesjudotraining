"""
לילה יפני — ניהול גיליון משתתפים.
format_sheet()  — מסדר ומעצב את הגיליון (כיתה → א-ב → צבע סניף)
add_from_csv()  — מוסיף משתתפים מקובץ CSV של Compete, בלי כפילויות
"""

import os, csv, pickle, base64
import googleapiclient.discovery

SPREADSHEET_ID = '1srujIboIUR3D0WQ9z1tHB9_d7jxs3Heoqz2KlwGLbdA'
SHEET_NAME = 'משתתפים'

GRADE_ORDER = {
    'גן': 0, 'א': 1, 'ב': 2, 'ג': 3, 'ד': 4, 'ה': 5,
    'ו': 6, 'ז': 7, 'ח': 8, 'ט': 9, 'י': 10, 'יא': 11, 'יב': 12
}

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

HEADER_BG   = {'red': 0.18, 'green': 0.22, 'blue': 0.38}
HEADER_TEXT = {'red': 1.0, 'green': 1.0, 'blue': 1.0}
ALT_ROW     = {'red': 0.95, 'green': 0.95, 'blue': 0.95}
WHITE       = {'red': 1.0, 'green': 1.0, 'blue': 1.0}


def _get_service():
    b64 = os.environ.get('GOOGLE_CREDS_B64')
    if b64:
        creds = pickle.loads(base64.b64decode(b64))
    else:
        with open(os.path.expanduser('~/.wolves_judo_token.pickle'), 'rb') as f:
            creds = pickle.load(f)
    return googleapiclient.discovery.build('sheets', 'v4', credentials=creds)


def _get_sheet_id(service):
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    return next(s['properties']['sheetId'] for s in meta['sheets']
                if s['properties']['title'] == SHEET_NAME)


def _read_students(service):
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f'{SHEET_NAME}!A1:F60'
    ).execute()
    rows = result.get('values', [])
    students = []
    for row in rows[1:]:
        if len(row) >= 2 and row[1].strip():
            students.append({
                'name':   row[1].strip(),
                'grade':  row[2].strip() if len(row) > 2 else '',
                'branch': row[3].strip().rstrip() if len(row) > 3 else '',
                'attend': row[4].strip() if len(row) > 4 else '',
                'notes':  row[5].strip() if len(row) > 5 else '',
            })
    return students


def get_students():
    service = _get_service()
    return _read_students(service)


def get_stats():
    students = get_students()
    by_branch = {}
    for s in students:
        b = s['branch'] or 'לא ידוע'
        by_branch[b] = by_branch.get(b, 0) + 1
    return {'total': len(students), 'by_branch': by_branch}


def add_student_direct(name, grade='', branch=''):
    """מוסיף משתתף ישירות (ללא CSV) ומעצב מחדש."""
    service = _get_service()
    existing = {s['name'] for s in _read_students(service)}
    if name in existing:
        return False  # כפיל
    current = _read_students(service)
    num = len(current) + 1
    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f'{SHEET_NAME}!A:F',
        valueInputOption='RAW',
        insertDataOption='INSERT_ROWS',
        body={'values': [[str(num), name, grade, branch, '', '']]}
    ).execute()
    format_sheet()
    return True


def format_sheet():
    """מסדר לפי כיתה ואלפביתית ומעצב עם צבעי סניף."""
    service = _get_service()
    sheet_id = _get_sheet_id(service)
    students = _read_students(service)

    students.sort(key=lambda x: (GRADE_ORDER.get(x['grade'], 99), x['name']))

    # Write sorted data
    new_values = [['', 'שם', 'כיתה', 'מועדון', 'נוכחות', 'הערות']]
    for i, s in enumerate(students, 1):
        new_values.append([str(i), s['name'], s['grade'], s['branch'], s['attend'], s['notes']])

    service.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID, range=f'{SHEET_NAME}!A1:F60'
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID, range=f'{SHEET_NAME}!A1',
        valueInputOption='RAW', body={'values': new_values}
    ).execute()

    num_rows = len(students) + 1
    req = []

    # Clear formatting
    req.append({'repeatCell': {
        'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': 60,
                   'startColumnIndex': 0, 'endColumnIndex': 6},
        'cell': {'userEnteredFormat': {}}, 'fields': 'userEnteredFormat',
    }})

    # Header
    req.append({'repeatCell': {
        'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': 1,
                   'startColumnIndex': 0, 'endColumnIndex': 6},
        'cell': {'userEnteredFormat': {
            'backgroundColor': HEADER_BG,
            'textFormat': {'foregroundColor': HEADER_TEXT, 'bold': True, 'fontSize': 12},
            'horizontalAlignment': 'CENTER', 'verticalAlignment': 'MIDDLE',
        }},
        'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)',
    }})

    # Student rows by branch color
    for i, s in enumerate(students):
        row_i = i + 1
        branch = s['branch']
        bg = BRANCH_BG.get(branch, ALT_ROW if i % 2 == 0 else WHITE)
        tc = BRANCH_TEXT.get(branch, {'red': 0.1, 'green': 0.1, 'blue': 0.1})
        req.append({'repeatCell': {
            'range': {'sheetId': sheet_id, 'startRowIndex': row_i, 'endRowIndex': row_i + 1,
                       'startColumnIndex': 0, 'endColumnIndex': 6},
            'cell': {'userEnteredFormat': {
                'backgroundColor': bg,
                'textFormat': {'foregroundColor': tc, 'fontSize': 11},
                'horizontalAlignment': 'CENTER', 'verticalAlignment': 'MIDDLE',
            }},
            'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)',
        }})

    # Name column bold + right-align
    req.append({'repeatCell': {
        'range': {'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': num_rows,
                   'startColumnIndex': 1, 'endColumnIndex': 2},
        'cell': {'userEnteredFormat': {
            'horizontalAlignment': 'RIGHT',
            'textFormat': {'bold': True, 'fontSize': 11},
        }},
        'fields': 'userEnteredFormat(horizontalAlignment,textFormat)',
    }})

    # Column widths
    for col, px in enumerate([45, 200, 60, 110, 80, 150]):
        req.append({'updateDimensionProperties': {
            'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS',
                       'startIndex': col, 'endIndex': col + 1},
            'properties': {'pixelSize': px}, 'fields': 'pixelSize',
        }})

    # Row heights
    req.append({'updateDimensionProperties': {
        'range': {'sheetId': sheet_id, 'dimension': 'ROWS',
                   'startIndex': 0, 'endIndex': num_rows},
        'properties': {'pixelSize': 36}, 'fields': 'pixelSize',
    }})

    # Borders
    thin  = {'style': 'SOLID', 'width': 1, 'color': {'red': 0.7, 'green': 0.7, 'blue': 0.7}}
    thick = {'style': 'SOLID_MEDIUM', 'width': 2, 'color': {'red': 0.3, 'green': 0.3, 'blue': 0.3}}
    req.append({'updateBorders': {
        'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': num_rows,
                   'startColumnIndex': 0, 'endColumnIndex': 6},
        'innerHorizontal': thin, 'innerVertical': thin,
        'top': thick, 'bottom': thick, 'left': thick, 'right': thick,
    }})

    # Freeze + RTL
    req.append({'updateSheetProperties': {
        'properties': {'sheetId': sheet_id,
                        'gridProperties': {'frozenRowCount': 1}, 'rightToLeft': True},
        'fields': 'gridProperties.frozenRowCount,rightToLeft',
    }})

    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID, body={'requests': req}
    ).execute()
    print(f'✓ עיצוב הושלם — {len(students)} ילדים')
    return students


def add_from_csv(csv_path: str):
    """
    מוסיף משתתפים מ-CSV של Compete (פורמט: שם משפחה שם פרטי).
    בודק כפילויות אוטומטית. אחרי הוספה מעצב מחדש.
    מחזיר (new_names, duplicate_names).
    """
    service = _get_service()
    existing = {s['name'] for s in _read_students(service)}

    new_entries = []
    duplicates = []

    with open(csv_path, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = row['שם'].strip()
            parts = raw.split()
            if not parts:
                continue
            full = parts[-1] + ' ' + ' '.join(parts[:-1]) if len(parts) >= 2 else raw
            full = ' '.join(full.split())

            if full in existing:
                duplicates.append(full)
            else:
                new_entries.append(full)
                existing.add(full)

    if new_entries:
        # Append to sheet
        current = _read_students(service)
        last_num = len(current)
        values = [[str(last_num + i + 1), name] for i, name in enumerate(new_entries)]
        service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f'{SHEET_NAME}!A:B',
            valueInputOption='RAW',
            insertDataOption='INSERT_ROWS',
            body={'values': values}
        ).execute()
        format_sheet()

    print(f'חדשים: {len(new_entries)} | כפילויות: {len(duplicates)}')
    for n in new_entries:
        print(f'  ➕ {n}')
    for n in duplicates:
        print(f'  ✓  {n}')

    return new_entries, duplicates


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        add_from_csv(sys.argv[1])
    else:
        format_sheet()
