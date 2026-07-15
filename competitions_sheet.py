"""תחרויות ואירועים — קריאה ועיצוב גיליון Google Sheets."""

import os, pickle, base64, time
from typing import Optional
import googleapiclient.discovery

SPREADSHEET_ID = '1SaUURPE3a2GgmYRtCTcr7zSUr_EbjeBFEYkk2Nwilow'

# ── עיצוב אחיד (זהה לנוכחות ולתוכניות אימון) ──────────────────────────────
_NAVY        = {"red": 0.10, "green": 0.15, "blue": 0.32}  # כותרת ראשית
_WHITE       = {"red": 1.0,  "green": 1.0,  "blue": 1.0}
_GOLD        = {"red": 0.90, "green": 0.60, "blue": 0.08}  # כותרת שם תחרות
_ROW_A       = {"red": 0.93, "green": 0.95, "blue": 0.99}  # שורות אי-זוגיות
_ROW_B       = {"red": 0.97, "green": 0.98, "blue": 1.00}  # שורות זוגיות
_MEDAL_GOLD  = {"red": 1.00, "green": 0.92, "blue": 0.60}  # 🥇 זהב
_MEDAL_SILV  = {"red": 0.90, "green": 0.93, "blue": 0.97}  # 🥈 כסף
_MEDAL_BRON  = {"red": 0.96, "green": 0.87, "blue": 0.78}  # 🥉 ארד
_BORDER      = {"red": 0.65, "green": 0.68, "blue": 0.76}
_BORDER_NAVY = _NAVY


def _get_service():
    b64 = os.environ.get('GOOGLE_CREDS_B64')
    if b64:
        creds = pickle.loads(base64.b64decode(b64 + "=="))
    else:
        with open(os.path.expanduser('~/token.pickle'), 'rb') as f:
            creds = pickle.load(f)
    return googleapiclient.discovery.build('sheets', 'v4', credentials=creds)


def _repeat(sid, r1, r2, c1, c2, fmt):
    return {"repeatCell": {
        "range": {"sheetId": sid, "startRowIndex": r1, "endRowIndex": r2,
                  "startColumnIndex": c1, "endColumnIndex": c2},
        "cell": {"userEnteredFormat": fmt},
        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,"
                  "verticalAlignment,wrapStrategy)",
    }}


def _repeat_nocolor(sid, r1, r2, c1, c2, fmt):
    """Like _repeat but does NOT touch backgroundColor — preserves manual cell colors."""
    return {"repeatCell": {
        "range": {"sheetId": sid, "startRowIndex": r1, "endRowIndex": r2,
                  "startColumnIndex": c1, "endColumnIndex": c2},
        "cell": {"userEnteredFormat": fmt},
        "fields": "userEnteredFormat(textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
    }}


def _border(sid, r1, r2, c1, c2, inner=True):
    thin   = {"style": "SOLID",        "color": _BORDER}
    thick  = {"style": "SOLID_MEDIUM", "color": _BORDER_NAVY}
    req = {"updateBorders": {
        "range": {"sheetId": sid, "startRowIndex": r1, "endRowIndex": r2,
                  "startColumnIndex": c1, "endColumnIndex": c2},
        "top": thick, "bottom": thick, "left": thick, "right": thick,
    }}
    if inner:
        req["updateBorders"]["innerHorizontal"] = thin
        req["updateBorders"]["innerVertical"]   = thin
    return req


def _col_width(sid, c1, c2, px):
    return {"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "COLUMNS",
                  "startIndex": c1, "endIndex": c2},
        "properties": {"pixelSize": px}, "fields": "pixelSize",
    }}


def _row_height(sid, r1, r2, px):
    return {"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "ROWS",
                  "startIndex": r1, "endIndex": r2},
        "properties": {"pixelSize": px}, "fields": "pixelSize",
    }}


def _freeze(sid, rows=1, cols=1):
    return {"updateSheetProperties": {
        "properties": {"sheetId": sid,
                       "gridProperties": {"frozenRowCount": rows, "frozenColumnCount": cols}},
        "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
    }}


def _medal_bg(val: str) -> Optional[dict]:
    """Return bg color for medal value, or None."""
    v = val.strip().lower()
    if "1" in v or "ראשון" in v or "זהב" in v or "🥇" in v:
        return _MEDAL_GOLD
    if "2" in v or "שני" in v or "כסף" in v or "🥈" in v:
        return _MEDAL_SILV
    if "3" in v or "שלישי" in v or "ארד" in v or "🥉" in v:
        return _MEDAL_BRON
    return None


HEADERS = ['#', 'שם', 'משפחה', 'מועדון', 'שנתון', 'משקל',
           'קרב 1', 'קרב 2', 'קרב 3', 'קרב 4', 'קרב 5', 'קרב 6', 'קרב 7',
           'מקום', 'מדליה', 'הערות']
COL_WIDTHS = [40, 90, 100, 100, 70, 70, 75, 75, 75, 75, 75, 75, 75, 65, 65, 130]
N_COLS = 16  # A–P


def design_tab(service, tab_name: str, sheet_id: int, rows: list):
    """Apply uniform design to one competition tab."""
    n_rows = max(len(rows), 2)
    n_content = n_rows - 1  # rows without header

    reqs = []

    # Freeze row 1, col 1
    reqs.append(_freeze(sheet_id, rows=1, cols=1))

    # Row heights
    reqs.append(_row_height(sheet_id, 0, 1, 38))
    if n_content > 0:
        reqs.append(_row_height(sheet_id, 1, n_rows, 30))

    # Column widths
    for c, px in enumerate(COL_WIDTHS):
        reqs.append(_col_width(sheet_id, c, c + 1, px))

    # ── Header row ──
    reqs.append(_repeat(sheet_id, 0, 1, 0, N_COLS, {
        "backgroundColor": _NAVY,
        "textFormat": {"bold": True, "fontSize": 11, "foregroundColor": _WHITE},
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
        "wrapStrategy": "WRAP",
    }))

    # ── Participant rows (alternating) ──
    for i in range(1, n_rows):
        row_bg = _ROW_A if i % 2 == 1 else _ROW_B

        # Cols A–F (0-5): identity columns — apply background + text
        reqs.append(_repeat(sheet_id, i, i + 1, 0, 6, {
            "backgroundColor": row_bg,
            "textFormat": {"fontSize": 10},
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
            "wrapStrategy": "WRAP",
        }))
        # שם + משפחה (cols B-C): right-align
        reqs.append(_repeat(sheet_id, i, i + 1, 1, 3, {
            "backgroundColor": row_bg,
            "textFormat": {"fontSize": 10},
            "horizontalAlignment": "RIGHT",
            "verticalAlignment": "MIDDLE",
        }))

        # Cols G–P (6-15): fight/result columns — text format ONLY, preserve manual colors
        reqs.append(_repeat_nocolor(sheet_id, i, i + 1, 6, N_COLS, {
            "textFormat": {"fontSize": 10, "bold": True},
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
            "wrapStrategy": "WRAP",
        }))

    # ── Borders ──
    if n_rows > 0:
        reqs.append(_border(sheet_id, 0, n_rows, 0, N_COLS))

    # Send
    for i in range(0, len(reqs), 400):
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": reqs[i:i + 400]}
        ).execute()


def ensure_headers(service, tab_name: str, sheet_id: int, rows: list):
    """Write HEADERS row if row 1 is empty or doesn't match."""
    current_headers = rows[0] if rows else []
    current_clean = [c.strip() for c in current_headers[:N_COLS]]
    expected_clean = HEADERS[:]
    expected_clean[0] = current_clean[0] if current_clean else ''  # '#' or '' is OK
    if current_clean == expected_clean:
        return  # Already correct

    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        body={"values": [HEADERS]}
    ).execute()


def design_all_tabs() -> str:
    """Design all competition tabs. Returns summary string."""
    service = _get_service()
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    tabs = [(s['properties']['title'], s['properties']['sheetId']) for s in meta['sheets']]

    results = []
    for tab_name, sid in tabs:
        try:
            data = service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID, range=f"'{tab_name}'!A1:J100"
            ).execute().get('values', [])

            ensure_headers(service, tab_name, sid, data)
            design_tab(service, tab_name, sid, data)
            n = len([r for r in data[1:] if len(r) >= 2 and r[0].strip()])
            results.append(f"✅ {tab_name} ({n} משתתפים)")
        except Exception as e:
            results.append(f"❌ {tab_name}: {e}")
        time.sleep(1.5)

    return "\n".join(results)


def get_competitions() -> list[dict]:
    """Return list of competition tabs with their participants."""
    service = _get_service()
    meta = service.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID, includeGridData=False
    ).execute()
    tabs = [s['properties']['title'] for s in meta['sheets'] if s['properties']['title'] != 'תחרות']
    result = []
    for tab in tabs:
        data = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range=f"'{tab}'!A1:P300"
        ).execute().get('values', [])
        participants = []
        for row in data[1:]:
            if not (row and row[0].strip()):
                continue
            first  = row[1].strip() if len(row) > 1 else ''
            last   = row[2].strip() if len(row) > 2 else ''
            club   = row[3].strip() if len(row) > 3 else ''
            year   = row[4].strip() if len(row) > 4 else ''
            weight = row[5].strip() if len(row) > 5 else ''
            fights = [row[c].strip() if c < len(row) else '' for c in range(6, 13)]
            place  = row[13].strip() if len(row) > 13 else ''
            medal  = row[14].strip() if len(row) > 14 else ''
            notes  = row[15].strip() if len(row) > 15 else ''
            results = [r for r in fights + [medal] if r]
            participants.append({
                'name': f"{first} {last}".strip(),
                'club': club, 'year': year, 'weight': weight,
                'results': results, 'place': place, 'medal': medal, 'notes': notes,
            })
        result.append({'competition': tab, 'participants': participants})
    return result


def get_stats() -> dict:
    """Return summary stats for all competitions."""
    comps = get_competitions()
    total_participants = sum(len(c['participants']) for c in comps)
    medals = {}
    by_competition = {}
    for c in comps:
        by_competition[c['competition']] = len(c['participants'])
        for p in c['participants']:
            for r in p.get('results', []):
                bg = _medal_bg(r)
                if bg == _MEDAL_GOLD:
                    medals['🥇 זהב'] = medals.get('🥇 זהב', 0) + 1
                elif bg == _MEDAL_SILV:
                    medals['🥈 כסף'] = medals.get('🥈 כסף', 0) + 1
                elif bg == _MEDAL_BRON:
                    medals['🥉 ארד'] = medals.get('🥉 ארד', 0) + 1
    return {
        'total_competitions': len(comps),
        'total_participants': total_participants,
        'by_competition': by_competition,
        'medals': medals,
        'competitions': comps,
    }


def get_tabs() -> list[str]:
    """Return list of competition tab names (excluding the main 'תחרות' tab)."""
    service = _get_service()
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    return [s['properties']['title'] for s in meta['sheets']
            if s['properties']['title'] != 'תחרות']


def add_participant(tab_name: str, first: str, last: str,
                    club: str, year: str, weight: str = '') -> int:
    """Append a participant row and return their sequential number."""
    service = _get_service()
    data = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{tab_name}'!A1:A300"
    ).execute().get('values', [])
    n = len([r for r in data[1:] if r and r[0].strip()])
    next_row = n + 2  # header=row1, 1-based participant count
    num = n + 1
    row = [num, first, last, club, year, weight] + [''] * 10
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{tab_name}'!A{next_row}:P{next_row}",
        valueInputOption='RAW',
        body={'values': [row]}
    ).execute()
    return num


def update_result(tab_name: str, participant_num: int, result: str) -> None:
    """Write result to the מדליה column (O) for the given participant number."""
    service = _get_service()
    sheet_row = participant_num + 1  # row 1 = header
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{tab_name}'!O{sheet_row}",
        valueInputOption='RAW',
        body={'values': [[result]]}
    ).execute()
