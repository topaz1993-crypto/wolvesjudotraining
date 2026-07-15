#!/usr/bin/env python3
"""
format_attendance_2026.py
עיצוב גיליונות נוכחות 2026-2027 + פונקציונלי 2026-2027
הרצה חד-פעמית: python3 format_attendance_2026.py
"""
import pickle, os, time
import googleapiclient.discovery

ATT_ID  = "1IyaoC4w9tkUCm8x0zL1QW4vyuiHH_xtyMEdRUG7mBao"
FUNC_ID = "14cZaNlaVoRTL-ddTyh6inwWotw5PL-uYww4YT9MYIfA"
TP_ID   = "1_a0RA2T5foLEVApDScLLqBqRn_gTpAR5-p0a5lDr7EE"

# ── Colors ─────────────────────────────────────────────────────────────────────
def c(r, g, b): return {"red": r, "green": g, "blue": b}
NAVY        = c(0.13, 0.19, 0.36)
ORANGE      = c(0.88, 0.42, 0.08)
WHITE       = c(1.0,  1.0,  1.0)
LIGHT_BLUE  = c(0.82, 0.85, 0.89)
ROW_A       = c(0.93, 0.95, 0.99)
ROW_B       = c(0.97, 0.98, 1.00)
DROPOUT_HDR = c(0.80, 0.20, 0.10)

# ── Training plans colors (reused from format_training_plans_2026) ─────────────
HDR_BG   = c(0.18, 0.18, 0.22)
GRP_BG   = c(0.16, 0.37, 0.62)
GRP_BG2  = c(0.20, 0.43, 0.68)
SEP_BG   = c(0.87, 0.87, 0.91)
ODD_BG   = c(0.93, 0.96, 1.00)
EVEN_BG  = c(1.00, 1.00, 1.00)
NOTES_BG = c(0.99, 0.99, 0.87)
DARK     = c(0.10, 0.12, 0.18)

ROW_TYPES = ["חימום", "תרגול", "קרבות", "משחק", "כוח", "נוסף"]

FUNC_TRAINING_GROUPS = [("08:00","ז-יב חדשים"),("09:00","ח-ט"),("10:00","י-יב")]


def get_service():
    with open(os.path.expanduser("~/.wolves_judo_token.pickle"), "rb") as f:
        creds = pickle.load(f)
    return googleapiclient.discovery.build("sheets", "v4", credentials=creds)


def get_sheet_map(service, sid):
    meta = service.spreadsheets().get(spreadsheetId=sid).execute()
    return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}


# ── Format helpers ─────────────────────────────────────────────────────────────
def rng(sid, r1, r2, c1, c2):
    return {"sheetId": sid, "startRowIndex": r1, "endRowIndex": r2,
            "startColumnIndex": c1, "endColumnIndex": c2}


def fmt_row(sid, row, bg, fg=None, bold=False, size=10, ncols=300):
    tf = {"fontSize": size, "bold": bold}
    if fg: tf["foregroundColor"] = fg
    cell = {"backgroundColor": bg, "textFormat": tf,
            "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE"}
    return {"repeatCell": {
        "range": rng(sid, row, row+1, 0, ncols),
        "cell": {"userEnteredFormat": cell},
        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)"
    }}


def fmt_range(sid, r1, r2, c1, c2, bg, fg=None, bold=False, size=10):
    tf = {"fontSize": size}
    if bold: tf["bold"] = True
    if fg: tf["foregroundColor"] = fg
    return {"repeatCell": {
        "range": rng(sid, r1, r2, c1, c2),
        "cell": {"userEnteredFormat": {"backgroundColor": bg, "textFormat": tf}},
        "fields": "userEnteredFormat(backgroundColor,textFormat)"
    }}


def col_width(sid, col, px):
    return {"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "COLUMNS",
                  "startIndex": col, "endIndex": col+1},
        "properties": {"pixelSize": px}, "fields": "pixelSize"
    }}


def row_height(sid, row, px):
    return {"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "ROWS",
                  "startIndex": row, "endIndex": row+1},
        "properties": {"pixelSize": px}, "fields": "pixelSize"
    }}


def freeze(sid, rows, cols):
    return {"updateSheetProperties": {
        "properties": {"sheetId": sid,
                       "gridProperties": {"frozenRowCount": rows, "frozenColumnCount": cols}},
        "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"
    }}


def merge(sid, row, c1, c2):
    return {"mergeCells": {
        "range": rng(sid, row, row+1, c1, c2),
        "mergeType": "MERGE_ALL"
    }}


# ── Attendance tab design ──────────────────────────────────────────────────────
def design_attendance_tab(service, spreadsheet_id, tab_name, tab_id):
    """Apply header styling + col widths to an attendance tab."""
    # Row 1: write הו"ק and משקל labels (D1, E1)
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!D1:E1",
        valueInputOption="RAW",
        body={"values": [['הו"ק', "משקל"]]}
    ).execute()
    # Row 2: identity column labels
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!A2:C2",
        valueInputOption="RAW",
        body={"values": [["מספר", "שם", "שם משפחה"]]}
    ).execute()

    reqs = [
        freeze(tab_id, 2, 3),
        # Row 1: full orange (month header + הו"ק + משקל)
        fmt_row(tab_id, 0, ORANGE, WHITE, bold=True, size=10),
        row_height(tab_id, 0, 28),
        # Row 2 A-E: navy (identity + הו"ק + משקל sub-header)
        {"repeatCell": {
            "range": rng(tab_id, 1, 2, 0, 5),
            "cell": {"userEnteredFormat": {
                "backgroundColor": NAVY,
                "textFormat": {"fontSize": 10, "bold": True, "foregroundColor": WHITE},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
        }},
        # Row 2 F+: light blue (date header)
        {"repeatCell": {
            "range": rng(tab_id, 1, 2, 5, 300),
            "cell": {"userEnteredFormat": {
                "backgroundColor": LIGHT_BLUE,
                "textFormat": {"fontSize": 9, "bold": True, "foregroundColor": NAVY},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
        }},
        row_height(tab_id, 1, 28),
        # Student rows: taller
        {"updateDimensionProperties": {
            "range": {"sheetId": tab_id, "dimension": "ROWS",
                      "startIndex": 2, "endIndex": 200},
            "properties": {"pixelSize": 26}, "fields": "pixelSize"
        }},
        # Col widths
        col_width(tab_id, 0, 50),   # A: מספר
        col_width(tab_id, 1, 110),  # B: שם
        col_width(tab_id, 2, 130),  # C: שם משפחה
        col_width(tab_id, 3, 44),   # D: הו"ק
        col_width(tab_id, 4, 44),   # E: משקל
    ]
    # Date cols F+ (index 5+)
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": tab_id, "dimension": "COLUMNS",
                  "startIndex": 5, "endIndex": 60},
        "properties": {"pixelSize": 38}, "fields": "pixelSize"
    }})

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": reqs}).execute()


# ── Dropout tab design ─────────────────────────────────────────────────────────
def design_dropout_tab(service, spreadsheet_id, tab_name, tab_id):
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!A1:E1",
        valueInputOption="RAW",
        body={"values": [["שם", "שם משפחה", "קבוצה", "תאריך הצטרפות", "תאריך פרישה"]]}
    ).execute()
    reqs = [
        fmt_row(tab_id, 0, DROPOUT_HDR, WHITE, bold=True, size=10),
        row_height(tab_id, 0, 28),
        col_width(tab_id, 0, 110),  # שם
        col_width(tab_id, 1, 120),  # שם משפחה
        col_width(tab_id, 2, 120),  # קבוצה
        col_width(tab_id, 3, 110),  # תאריך הצטרפות
        col_width(tab_id, 4, 110),  # תאריך פרישה
    ]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": reqs}).execute()


# ── Training plans tab design (for פונקציונלי "תוכניות אימון") ─────────────────
def design_training_tab(service, spreadsheet_id, tab_name, tab_id, groups):
    from datetime import date
    today = date.today().strftime("%d/%m/%Y")
    values = []
    meta = []

    values.append([f"עודכן: {today}", "שעה", "קבוצה", "הערות"])
    meta.append("col_header")

    for g_idx, (time_str, group_name) in enumerate(groups):
        values.append([group_name, f"{group_name}   •   {time_str}", "", ""])
        meta.append(("grp_hdr", g_idx))
        for i, rt in enumerate(ROW_TYPES):
            values.append([time_str, group_name, "", ""])
            meta.append(("data", i))
        values.append(["", "", "", ""])
        meta.append("sep")

    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=f"'{tab_name}'!A:ZZ", body={}).execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        body={"values": values}
    ).execute()
    time.sleep(0.4)

    reqs = [
        freeze(tab_id, 1, 2),
        col_width(tab_id, 0, 65),
        col_width(tab_id, 1, 145),
        col_width(tab_id, 2, 165),
        {"repeatCell": {  # notes col bg
            "range": rng(tab_id, 1, 500, 2, 3),
            "cell": {"userEnteredFormat": {"backgroundColor": NOTES_BG}},
            "fields": "userEnteredFormat.backgroundColor"
        }},
    ]

    for row_i, m in enumerate(meta):
        if m == "col_header":
            reqs.append(fmt_range(tab_id, row_i, row_i+1, 0, 80, HDR_BG, WHITE, bold=True, size=9))
            reqs.append(row_height(tab_id, row_i, 28))
        elif isinstance(m, tuple) and m[0] == "grp_hdr":
            bg = GRP_BG if m[1] % 2 == 0 else GRP_BG2
            reqs.append(fmt_range(tab_id, row_i, row_i+1, 0, 80, bg, WHITE, bold=True, size=11))
            reqs.append(merge(tab_id, row_i, 2, 60))
            reqs.append(row_height(tab_id, row_i, 32))
        elif isinstance(m, tuple) and m[0] == "data":
            bg = ODD_BG if m[1] % 2 == 0 else EVEN_BG
            reqs.append(fmt_range(tab_id, row_i, row_i+1, 0, 80, bg, DARK, size=10))
            reqs.append(fmt_range(tab_id, row_i, row_i+1, 2, 3, NOTES_BG))
            reqs.append(row_height(tab_id, row_i, 24))
        elif m == "sep":
            reqs.append(fmt_range(tab_id, row_i, row_i+1, 0, 80, SEP_BG))
            reqs.append(row_height(tab_id, row_i, 8))

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": reqs}).execute()
    time.sleep(0.5)


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    service = get_service()

    # 1. נוכחות 2026-2027
    print("📋 נוכחות 2026-2027 ...")
    att_map = get_sheet_map(service, ATT_ID)
    for tab_name, tab_id in att_map.items():
        print(f"  {tab_name} ...", end=" ", flush=True)
        if tab_name.startswith("פורשים"):
            design_dropout_tab(service, ATT_ID, tab_name, tab_id)
        else:
            design_attendance_tab(service, ATT_ID, tab_name, tab_id)
        time.sleep(0.3)
        print("✅")

    # 2. פונקציונלי 2026-2027
    print("\n📋 פונקציונלי 2026-2027 ...")
    func_map = get_sheet_map(service, FUNC_ID)
    for tab_name, tab_id in func_map.items():
        print(f"  {tab_name} ...", end=" ", flush=True)
        if tab_name == "תוכניות אימון":
            design_training_tab(service, FUNC_ID, tab_name, tab_id, FUNC_TRAINING_GROUPS)
        elif tab_name == "פורשים":
            design_dropout_tab(service, FUNC_ID, tab_name, tab_id)
        else:
            design_attendance_tab(service, FUNC_ID, tab_name, tab_id)
        time.sleep(0.3)
        print("✅")

    print(f"\n✅ הושלם:")
    print(f"   נוכחות:      https://docs.google.com/spreadsheets/d/{ATT_ID}/edit")
    print(f"   פונקציונלי:  https://docs.google.com/spreadsheets/d/{FUNC_ID}/edit")


if __name__ == "__main__":
    main()
