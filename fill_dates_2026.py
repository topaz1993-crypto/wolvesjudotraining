#!/usr/bin/env python3
"""
fill_dates_2026.py
מילוי תאריכי אימון בגיליון נוכחות 2026-2027.
שורה 1 = שמות חודשים (ספטמבר, אוקטובר, ...) — בתא הראשון של כל חודש בלבד.
שורה 2 = מספרי ימים (1, 4, 8, ...) לפי לוח האימונים של כל קבוצה.
עמודות D=הו"ק, E=משקל נשארות. תאריכים מתחילים מ-F.
"""

import pickle, os, time
from datetime import date, timedelta
import googleapiclient.discovery

ATT_ID  = "1IyaoC4w9tkUCm8x0zL1QW4vyuiHH_xtyMEdRUG7mBao"
FUNC_ID = "14cZaNlaVoRTL-ddTyh6inwWotw5PL-uYww4YT9MYIfA"

SEASON_START = date(2026, 9, 1)
SEASON_END   = date(2027, 7, 31)

HEBREW_MONTHS = {
    9: "ספטמבר", 10: "אוקטובר", 11: "נובמבר", 12: "דצמבר",
    1: "ינואר",  2: "פברואר",  3: "מרץ",    4: "אפריל",
    5: "מאי",    6: "יוני",    7: "יולי",
}

# Python weekdays: Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
ATT_WEEKDAYS = {
    # סירקין — שני + חמישי
    "סירקין-ג-ד":              [0, 3],
    "סירקין-ה-ו":              [0, 3],
    "סירקין-א-ב":              [0, 3],
    "סירקין-ז-בוגרים":         [0, 3],
    # סירקין — חמישי בלבד
    "סירקין-גן חובה":          [3],
    "סירקין-טרום חובה":        [3],
    # סירקין — רביעי (איפון פייט)
    "סירקין-איפון פייט ב-ג":  [2],
    "סירקין-איפון פייט ד-ו":  [2],
    # סירקין — שישי (נבחרת)
    "סירקין-נבחרת":            [4],
    # נווה ירק — שלישי
    "נווה ירק-גנים":           [1],
    "נווה ירק-ג-ז":            [1],
    "נווה ירק-א-ב":            [1],
    "נווה ירק-ז-בוגרים":       [1],
    # חגור — ראשון
    "חגור-ב-ה":                [6],
    "חגור-גנים-א":             [6],
    # אהרונוביץ — רביעי
    "אהרונוביץ-א-ב":           [2],
    "אהרונוביץ-ג-ו":           [2],
    # גבעת השלושה — שישי
    "נבחרת בוגרת - גבעת השלושה": [4],
}

FUNC_WEEKDAYS = {
    "נוכחות-ז-יב חדשים": [2, 4],  # רביעי + שישי
    "נוכחות-ח-ט":         [2, 4],
    "נוכחות-י-יב":        [2, 4],
}

NAVY       = {"red": 0.13, "green": 0.19, "blue": 0.36}
ORANGE     = {"red": 0.88, "green": 0.42, "blue": 0.08}
WHITE      = {"red": 1.0,  "green": 1.0,  "blue": 1.0}
LIGHT_BLUE = {"red": 0.82, "green": 0.85, "blue": 0.89}


def col_letter(n):
    """Convert 0-based column index to A, B, ..., Z, AA, AB, ..."""
    result = ""
    n += 1
    while n > 0:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result


def get_service():
    with open(os.path.expanduser("~/token.pickle"), "rb") as f:
        creds = pickle.load(f)
    return googleapiclient.discovery.build("sheets", "v4", credentials=creds)


def training_dates(weekdays):
    """All training dates in the season for the given weekdays, sorted."""
    d, result = SEASON_START, []
    while d <= SEASON_END:
        if d.weekday() in weekdays:
            result.append(d)
        d += timedelta(days=1)
    return result


def build_header_rows(dates):
    """
    Returns (month_row, date_row) — parallel lists.
    month_row: month name at the FIRST date of each month, empty string otherwise.
    date_row:  day number as string.
    """
    month_row, date_row = [], []
    last_month = None
    for d in dates:
        month_label = HEBREW_MONTHS[d.month]
        month_row.append(month_label if d.month != last_month else "")
        date_row.append(str(d.day))
        last_month = d.month
    return month_row, date_row


def write_dates(service, sid, sheet_id, tab_name, weekdays):
    """Write month names (row 1) and date numbers (row 2) starting at column F."""
    dates = training_dates(weekdays)
    if not dates:
        return 0

    month_row, date_row = build_header_rows(dates)

    n = len(dates)
    # Columns: F = index 5 (0-based).  Last col = 5 + n - 1.
    first_col = "F"
    last_col  = col_letter(5 + n - 1)

    # Write values
    service.spreadsheets().values().update(
        spreadsheetId=sid,
        range=f"'{tab_name}'!{first_col}1:{last_col}1",
        valueInputOption="RAW",
        body={"values": [month_row]}
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=sid,
        range=f"'{tab_name}'!{first_col}2:{last_col}2",
        valueInputOption="RAW",
        body={"values": [date_row]}
    ).execute()

    # Apply formatting: row 1 orange, row 2 light blue — for these specific cols only
    def rng(r1, r2, c1, c2):
        return {"sheetId": sheet_id,
                "startRowIndex": r1, "endRowIndex": r2,
                "startColumnIndex": c1, "endColumnIndex": c2}

    reqs = [
        # Row 1 date area: orange bg, white bold text, center
        {"repeatCell": {
            "range": rng(0, 1, 5, 5 + n),
            "cell": {"userEnteredFormat": {
                "backgroundColor": ORANGE,
                "textFormat": {"fontSize": 9, "bold": True, "foregroundColor": WHITE},
                "horizontalAlignment": "LEFT",
                "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
        }},
        # Row 2 date area: light blue bg, navy bold text, center
        {"repeatCell": {
            "range": rng(1, 2, 5, 5 + n),
            "cell": {"userEnteredFormat": {
                "backgroundColor": LIGHT_BLUE,
                "textFormat": {"fontSize": 9, "bold": True, "foregroundColor": NAVY},
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
        }},
    ]
    service.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": reqs}).execute()
    return n


def process_sheet(service, sid, weekday_map, label):
    print(f"\n📋 {label}")
    meta = service.spreadsheets().get(spreadsheetId=sid).execute()
    tab_map = {s["properties"]["title"]: s["properties"]["sheetId"]
               for s in meta["sheets"]}

    for tab_name, weekdays in weekday_map.items():
        sheet_id = tab_map.get(tab_name)
        if sheet_id is None:
            print(f"  ⚠  {tab_name} — לא נמצא")
            continue
        print(f"  {tab_name} ...", end=" ", flush=True)
        try:
            n = write_dates(service, sid, sheet_id, tab_name, weekdays)
            print(f"✅ {n} תאריכים")
            time.sleep(1.0)
        except Exception as e:
            print(f"❌ {e}")
            time.sleep(2.0)


if __name__ == "__main__":
    svc = get_service()
    process_sheet(svc, ATT_ID,  ATT_WEEKDAYS,  "נוכחות 2026-2027")
    process_sheet(svc, FUNC_ID, FUNC_WEEKDAYS, "פונקציונלי 2026-2027")
    print("\n✅ הושלם.")
