#!/usr/bin/env python3
"""
create_sheets_2026.py
יצירת גיליונות Google Sheets לעונה 2026-2027.
הרצה חד-פעמית מקומית: python3 create_sheets_2026.py
"""

import pickle
import os
import time
import googleapiclient.discovery

# ── Auth ───────────────────────────────────────────────────────────────────────

def get_service():
    pickle_path = os.path.expanduser("~/token.pickle")
    with open(pickle_path, "rb") as f:
        creds = pickle.load(f)
    return googleapiclient.discovery.build("sheets", "v4", credentials=creds)


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_sheet_ids(service, spreadsheet_id):
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return {s["properties"]["title"]: s["properties"]["sheetId"]
            for s in meta["sheets"]}


def create_spreadsheet(service, title):
    result = service.spreadsheets().create(
        body={"properties": {"title": title}}
    ).execute()
    return result["spreadsheetId"]


def rename_first_sheet(service, spreadsheet_id, new_name):
    sheet_ids = get_sheet_ids(service, spreadsheet_id)
    first_id = list(sheet_ids.values())[0]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": first_id, "title": new_name},
            "fields": "title"
        }}]}
    ).execute()


def add_sheets(service, spreadsheet_id, names):
    requests = [{"addSheet": {"properties": {"title": n}}} for n in names]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests}
    ).execute()


def freeze_attendance_tab(service, spreadsheet_id, sheet_id):
    """2 שורות קפואות + 3 עמודות קפואות."""
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {"frozenRowCount": 2, "frozenColumnCount": 3}
            },
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"
        }}]}
    ).execute()


def write_range(service, spreadsheet_id, range_str, values):
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_str,
        valueInputOption="RAW",
        body={"values": values}
    ).execute()
    time.sleep(0.3)


# ── Tab lists ──────────────────────────────────────────────────────────────────

# נוכחות 2026-2027 — 23 טאבים
ATT_TABS = [
    # סירקין
    "סירקין-ג-ד", "סירקין-ה-ו", "סירקין-א-ב",
    "סירקין-טרום חובה", "סירקין-גן חובה", "סירקין-ז-בוגרים",
    "סירקין-איפון פייט ב-ג", "סירקין-איפון פייט ד-ו", "סירקין-נבחרת",
    "פורשים-סירקין",
    # נווה ירק
    "נווה ירק-גנים", "נווה ירק-ג-ז", "נווה ירק-א-ב", "נווה ירק-ז-בוגרים",
    "פורשים-נווה ירק",
    # חגור
    "חגור-ב-ה", "חגור-גנים-א",
    "פורשים-חגור",
    # אהרונוביץ
    "אהרונוביץ-א-ב", "אהרונוביץ-ג-ו",
    "פורשים-אהרונוביץ",
    # גבעת השלושה
    "נבחרת בוגרת - גבעת השלושה",
    "פורשים-גבעת השלושה",
]

# פונקציונלי 2026-2027 — 5 טאבים
FUNC_TABS = [
    "נוכחות-ז-יב חדשים", "נוכחות-ח-ט", "נוכחות-י-יב",
    "פורשים",
    "תוכניות אימון",
]

# ── Training plan data ─────────────────────────────────────────────────────────
# (שעה, שם קבוצה) — 6 שורות לכל קבוצה (חימום/תרגול/קרבות/משחק/כוח/נוסף)

ROW_TYPES = ["חימום", "תרגול", "קרבות", "משחק", "כוח", "נוסף"]

TRAINING_GROUPS = {
    "סירקין": [
        ("14:30", "ה-ו"),
        ("15:30", "ג-ד"),
        ("16:30", "א-ב"),
        ("17:15", "טרום חובה"),
        ("17:15", "גן חובה"),
        ("18:00", "ז-בוגרים"),
    ],
    "נווה ירק": [
        ("15:15", "ז-בוגרים"),
        ("16:00", "גנים"),
        ("16:45", "ג-ז"),
        ("17:45", "א-ב"),
    ],
    "חגור": [
        ("16:30", "ב-ה"),
        ("17:15", "גנים-א"),
    ],
    "אהרונוביץ": [
        ("13:00", "א-ב"),
        ("13:50", "ג-ו"),
    ],
    "איפון פייט": [
        ("18:30", "ב-ג"),
        ("19:15", "ד-ו"),
    ],
    "נבחרת": [
        ("13:15", "נבחרת"),
        ("15:30", "נבחרת בוגרת"),
    ],
}

FUNC_TRAINING_GROUPS = {
    "תוכניות אימון": [
        ("08:00", "ז-יב חדשים"),
        ("09:00", "ח-ט"),
        ("10:00", "י-יב"),
    ],
}

DROPOUT_HEADERS = [["שם", "שם משפחה", "קבוצה", "תאריך הצטרפות", "תאריך פרישה"]]


# ── Setup functions ────────────────────────────────────────────────────────────

def setup_attendance_spreadsheet(service, spreadsheet_id, all_tabs):
    """הקפאת שורות/עמודות לטאבי נוכחות; כותרות לטאבי פורשים."""
    sheet_ids = get_sheet_ids(service, spreadsheet_id)
    freeze_reqs = []

    for name in all_tabs:
        sid = sheet_ids.get(name)
        if sid is None:
            print(f"  ⚠️  טאב לא נמצא: {name}")
            continue

        if name.startswith("פורשים"):
            write_range(service, spreadsheet_id, f"'{name}'!A1:E1", DROPOUT_HEADERS)
        else:
            freeze_reqs.append({"updateSheetProperties": {
                "properties": {
                    "sheetId": sid,
                    "gridProperties": {"frozenRowCount": 2, "frozenColumnCount": 3}
                },
                "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"
            }})

    if freeze_reqs:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": freeze_reqs}
        ).execute()


def setup_training_spreadsheet(service, spreadsheet_id, groups_by_tab):
    """מילוי עמודות שעה + קבוצה בטאבי תוכניות אימון."""
    for tab_name, groups in groups_by_tab.items():
        rows = []
        for time_str, group_name in groups:
            for _ in ROW_TYPES:
                rows.append([time_str, group_name])
        write_range(service, spreadsheet_id, f"'{tab_name}'!A1:B{len(rows)}", rows)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    service = get_service()
    results = {}

    # 1. נוכחות 2026-2027
    print("📋 יוצר: נוכחות 2026-2027 ...")
    att_id = create_spreadsheet(service, "נוכחות 2026-2027")
    rename_first_sheet(service, att_id, ATT_TABS[0])
    add_sheets(service, att_id, ATT_TABS[1:])
    time.sleep(1)
    setup_attendance_spreadsheet(service, att_id, ATT_TABS)
    results["נוכחות 2026-2027"] = att_id
    print(f"   ✅ https://docs.google.com/spreadsheets/d/{att_id}/edit")

    # 2. פונקציונלי 2026-2027
    print("📋 יוצר: פונקציונלי 2026-2027 ...")
    func_id = create_spreadsheet(service, "פונקציונלי 2026-2027")
    rename_first_sheet(service, func_id, FUNC_TABS[0])
    add_sheets(service, func_id, FUNC_TABS[1:])
    time.sleep(1)
    # נוכחות + פורשים
    setup_attendance_spreadsheet(service, func_id, FUNC_TABS)
    # תוכניות אימון
    setup_training_spreadsheet(service, func_id, FUNC_TRAINING_GROUPS)
    results["פונקציונלי 2026-2027"] = func_id
    print(f"   ✅ https://docs.google.com/spreadsheets/d/{func_id}/edit")

    # 3. תוכניות אימון 2026-2027
    print("📋 יוצר: תוכניות אימון 2026-2027 ...")
    tp_tabs = list(TRAINING_GROUPS.keys())
    tp_id = create_spreadsheet(service, "תוכניות אימון 2026-2027")
    rename_first_sheet(service, tp_id, tp_tabs[0])
    add_sheets(service, tp_id, tp_tabs[1:])
    time.sleep(1)
    setup_training_spreadsheet(service, tp_id, TRAINING_GROUPS)
    results["תוכניות אימון 2026-2027"] = tp_id
    print(f"   ✅ https://docs.google.com/spreadsheets/d/{tp_id}/edit")

    # סיכום IDs
    print("\n" + "="*60)
    print("✅ שמור את ה-IDs האלה לשלב 2 (עדכון הבוט):")
    print("="*60)
    for name, sid in results.items():
        print(f"  {name}:")
        print(f"    ID:  {sid}")
        print(f"    URL: https://docs.google.com/spreadsheets/d/{sid}/edit")


if __name__ == "__main__":
    main()
