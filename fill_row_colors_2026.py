#!/usr/bin/env python3
"""
fill_row_colors_2026.py
צביעת שורות תלמידים (שורות 3-200) בגיליונות נוכחות 2026-2027.
עמודות A-E: ROW_A (כחול-בהיר)
עמודות F+: לבן (לא נוגע — שם ייכתבו הנוכחויות)
"""
import pickle, os, time
import googleapiclient.discovery

ATT_ID  = "1IyaoC4w9tkUCm8x0zL1QW4vyuiHH_xtyMEdRUG7mBao"
FUNC_ID = "14cZaNlaVoRTL-ddTyh6inwWotw5PL-uYww4YT9MYIfA"

ROW_A = {"red": 0.93, "green": 0.95, "blue": 0.99}
WHITE = {"red": 1.0,  "green": 1.0,  "blue": 1.0}


def get_service():
    with open(os.path.expanduser("~/token.pickle"), "rb") as f:
        creds = pickle.load(f)
    return googleapiclient.discovery.build("sheets", "v4", credentials=creds)


def color_student_rows(service, sid, sheet_id):
    reqs = [
        # כל העמודות A+: ROW_A
        {"repeatCell": {
            "range": {"sheetId": sheet_id,
                      "startRowIndex": 2, "endRowIndex": 200,
                      "startColumnIndex": 0, "endColumnIndex": 300},
            "cell": {"userEnteredFormat": {
                "backgroundColor": ROW_A,
                "textFormat": {"fontSize": 10},
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
        }},
    ]
    service.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": reqs}).execute()


def process(service, sid, label):
    print(f"\n📋 {label}")
    meta = service.spreadsheets().get(spreadsheetId=sid).execute()
    for s in meta["sheets"]:
        name = s["properties"]["title"]
        if name.startswith("פורשים") or name == "תוכניות אימון":
            continue
        sheet_id = s["properties"]["sheetId"]
        print(f"  {name} ...", end=" ", flush=True)
        try:
            color_student_rows(service, sid, sheet_id)
            print("✅")
        except Exception as e:
            print(f"❌ {e}")
        time.sleep(0.5)


if __name__ == "__main__":
    svc = get_service()
    process(svc, ATT_ID,  "נוכחות 2026-2027")
    process(svc, FUNC_ID, "פונקציונלי 2026-2027")
    print("\n✅ הושלם.")
