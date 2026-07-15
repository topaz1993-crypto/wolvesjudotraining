#!/usr/bin/env python3
"""
format_training_plans_2026.py
עיצוב + מבנה חדש לגיליון תוכניות אימון 2026-2027
הרצה חד-פעמית: python3 format_training_plans_2026.py
"""
import pickle, os, time
from datetime import date
import googleapiclient.discovery

TP_ID = "1_a0RA2T5foLEVApDScLLqBqRn_gTpAR5-p0a5lDr7EE"

ROW_TYPES = ["חימום", "תרגול", "קרבות", "משחק", "כוח", "נוסף"]

TRAINING_GROUPS = {
    "סירקין":     [("14:30","ה-ו"),("15:30","ג-ד"),("16:30","א-ב"),
                   ("17:15","טרום חובה"),("17:15","גן חובה"),("18:00","ז-בוגרים")],
    "נווה ירק":   [("15:15","ז-בוגרים"),("16:00","גנים"),("16:45","ג-ז"),("17:45","א-ב")],
    "חגור":       [("16:30","ב-ה"),("17:15","גנים-א")],
    "אהרונוביץ":  [("13:00","א-ב"),("13:50","ג-ו")],
    "איפון פייט": [("18:30","ב-ג"),("19:15","ד-ו")],
    "נבחרת":      [("13:15","נבחרת"),("15:30","נבחרת בוגרת")],
}

# ── Colors ─────────────────────────────────────────────────────────────────────
def c(r, g, b): return {"red": r, "green": g, "blue": b}

HDR_BG    = c(0.18, 0.18, 0.22)   # col header: very dark
GRP_BG    = c(0.16, 0.37, 0.62)   # group header: medium blue
GRP_BG2   = c(0.20, 0.43, 0.68)   # alternate group header
SEP_BG    = c(0.87, 0.87, 0.91)   # separator row
ODD_BG    = c(0.93, 0.96, 1.00)   # odd data row
EVEN_BG   = c(1.00, 1.00, 1.00)   # even data row
NOTES_BG  = c(0.99, 0.99, 0.87)   # notes column tint
WHITE     = c(1.0,  1.0,  1.0)
DARK      = c(0.10, 0.12, 0.18)

# ── Helpers ────────────────────────────────────────────────────────────────────
def get_service():
    with open(os.path.expanduser("~/token.pickle"), "rb") as f:
        creds = pickle.load(f)
    return googleapiclient.discovery.build("sheets", "v4", credentials=creds)


def get_sheet_ids(service):
    meta = service.spreadsheets().get(spreadsheetId=TP_ID).execute()
    return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}


def rng(sid, r1, r2, c1, c2):
    return {"sheetId": sid, "startRowIndex": r1, "endRowIndex": r2,
            "startColumnIndex": c1, "endColumnIndex": c2}


def fmt_row(sid, row, bg=None, fg=None, bold=False, size=10, ncols=80):
    cell_fmt = {}
    if bg:
        cell_fmt["backgroundColor"] = bg
    tf = {"fontSize": size}
    if fg:
        tf["foregroundColor"] = fg
    if bold:
        tf["bold"] = True
    cell_fmt["textFormat"] = tf
    return {"repeatCell": {
        "range": rng(sid, row, row+1, 0, ncols),
        "cell": {"userEnteredFormat": cell_fmt},
        "fields": "userEnteredFormat(backgroundColor,textFormat)"
    }}


def fmt_cell(sid, row, col, bg=None, fg=None, bold=False):
    cell_fmt = {}
    if bg:
        cell_fmt["backgroundColor"] = bg
    if fg or bold:
        cell_fmt["textFormat"] = {}
        if fg:
            cell_fmt["textFormat"]["foregroundColor"] = fg
        if bold:
            cell_fmt["textFormat"]["bold"] = bold
    return {"repeatCell": {
        "range": rng(sid, row, row+1, col, col+1),
        "cell": {"userEnteredFormat": cell_fmt},
        "fields": "userEnteredFormat(backgroundColor,textFormat)"
    }}


def merge(sid, row, c1, c2):
    return {"mergeCells": {
        "range": rng(sid, row, row+1, c1, c2),
        "mergeType": "MERGE_ALL"
    }}


def row_height(sid, row, px):
    return {"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "ROWS",
                  "startIndex": row, "endIndex": row+1},
        "properties": {"pixelSize": px},
        "fields": "pixelSize"
    }}


def col_width(sid, col, px):
    return {"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "COLUMNS",
                  "startIndex": col, "endIndex": col+1},
        "properties": {"pixelSize": px},
        "fields": "pixelSize"
    }}


def freeze(sid, rows, cols):
    return {"updateSheetProperties": {
        "properties": {"sheetId": sid,
                       "gridProperties": {"frozenRowCount": rows, "frozenColumnCount": cols}},
        "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"
    }}


def col_bg(sid, col, bg, r1=1, r2=500):
    return {"repeatCell": {
        "range": rng(sid, r1, r2, col, col+1),
        "cell": {"userEnteredFormat": {"backgroundColor": bg}},
        "fields": "userEnteredFormat.backgroundColor"
    }}


# ── Tab builder ────────────────────────────────────────────────────────────────
def build_tab(service, tab_name, tab_id, groups):
    today = date.today().strftime("%d/%m/%Y")
    values = []
    meta   = []

    # Row 0 — column headers
    values.append([f"עודכן: {today}", "שעה", "קבוצה", "הערות"])
    meta.append("col_header")

    for g_idx, (time_str, group_name) in enumerate(groups):
        # Group header
        values.append([group_name, f"{group_name}   •   {time_str}", "", ""])
        meta.append(("grp_hdr", g_idx))

        # 6 data rows
        for i, rt in enumerate(ROW_TYPES):
            values.append([time_str, group_name, "", ""])
            meta.append(("data", i))

        # Separator
        values.append(["", "", "", ""])
        meta.append("sep")

    # ── Write values ──────────────────────────────────────────────────────────
    service.spreadsheets().values().clear(
        spreadsheetId=TP_ID, range=f"'{tab_name}'!A:ZZ", body={}).execute()
    service.spreadsheets().values().update(
        spreadsheetId=TP_ID,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        body={"values": values}
    ).execute()
    time.sleep(0.4)

    # ── Format requests ───────────────────────────────────────────────────────
    reqs = []

    # Freeze + col widths
    reqs.append(freeze(tab_id, 1, 2))
    reqs.append(col_width(tab_id, 0, 65))    # A: שעה
    reqs.append(col_width(tab_id, 1, 145))   # B: קבוצה
    reqs.append(col_width(tab_id, 2, 165))   # C: הערות
    reqs.append(row_height(tab_id, 0, 30))   # col-header row height

    # Notes column background (C) for all data rows
    reqs.append(col_bg(tab_id, 2, NOTES_BG, r1=1))

    # Row-by-row
    for row_i, m in enumerate(meta):
        if m == "col_header":
            reqs.append(fmt_row(tab_id, row_i, bg=HDR_BG, fg=WHITE, bold=True, size=9))
            reqs.append(row_height(tab_id, row_i, 28))

        elif isinstance(m, tuple) and m[0] == "grp_hdr":
            bg = GRP_BG if m[1] % 2 == 0 else GRP_BG2
            reqs.append(fmt_row(tab_id, row_i, bg=bg, fg=WHITE, bold=True, size=11))
            reqs.append(merge(tab_id, row_i, 2, 60))   # merge C onwards (frozen cols A:B stay separate)
            reqs.append(row_height(tab_id, row_i, 32))

        elif isinstance(m, tuple) and m[0] == "data":
            bg = ODD_BG if m[1] % 2 == 0 else EVEN_BG
            reqs.append(fmt_row(tab_id, row_i, bg=bg, fg=DARK, size=10))
            reqs.append(fmt_cell(tab_id, row_i, 2, bg=NOTES_BG))  # notes cell
            reqs.append(row_height(tab_id, row_i, 24))

        elif m == "sep":
            reqs.append(fmt_row(tab_id, row_i, bg=SEP_BG))
            reqs.append(row_height(tab_id, row_i, 8))

    service.spreadsheets().batchUpdate(
        spreadsheetId=TP_ID, body={"requests": reqs}).execute()
    time.sleep(0.5)


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    service = get_service()
    sheet_ids = get_sheet_ids(service)

    for tab_name, groups in TRAINING_GROUPS.items():
        tid = sheet_ids.get(tab_name)
        if tid is None:
            print(f"  ⚠️  לא נמצא: {tab_name}")
            continue
        print(f"  עיצוב: {tab_name} ...", end=" ", flush=True)
        build_tab(service, tab_name, tid, groups)
        print("✅")

    print(f"\n✅ הגיליון מוכן:")
    print(f"   https://docs.google.com/spreadsheets/d/{TP_ID}/edit")


if __name__ == "__main__":
    main()
