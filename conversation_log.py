"""
conversation_log.py — שומר כל שיחה עם הבוט ל-Google Sheet.

גיליון: "לוג שיחות בוט" — נוצר אוטומטית אם לא קיים.
עמודות: תאריך | שעה | הודעת משתמש | תגובת הבוט | פעולה שבוצעה | הצלחה | הערות
"""

import os
import pickle
import base64
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_DATA_DIR  = Path("/data") if Path("/data").exists() else Path(".")
_ID_FILE   = _DATA_DIR / "conv_log_sheet_id.txt"

SHEET_TITLE = "לוג שיחות בוט"
HEADERS     = ["תאריך", "שעה", "הודעת משתמש", "תגובת הבוט", "פעולה שבוצעה", "הצלחה", "הערות"]


def _get_service():
    b64 = os.environ.get("GOOGLE_CREDS_B64")
    if b64:
        creds = pickle.loads(base64.b64decode(b64 + "=="))
    else:
        import glob
        p = os.path.expanduser("~/token.pickle")
        with open(p, "rb") as f:
            creds = pickle.load(f)
    from googleapiclient.discovery import build
    return build("sheets", "v4", credentials=creds)


def _get_or_create_sheet() -> str:
    """מחזיר ID של גיליון הלוג — יוצר אחד אם לא קיים."""
    if _ID_FILE.exists():
        return _ID_FILE.read_text().strip()

    svc = _get_service()
    body = {
        "properties": {"title": SHEET_TITLE},
        "sheets": [{"properties": {"title": "שיחות", "rightToLeft": True}}]
    }
    sheet = svc.spreadsheets().create(body=body, fields="spreadsheetId").execute()
    sheet_id = sheet["spreadsheetId"]

    # כתיבת כותרות
    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range="שיחות!A1",
        valueInputOption="RAW",
        body={"values": [HEADERS]}
    ).execute()

    # עיצוב כותרת
    svc.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [{
            "repeatCell": {
                "range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": {"red": 0.18, "green": 0.22, "blue": 0.38},
                    "textFormat": {
                        "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                        "bold": True
                    }
                }},
                "fields": "userEnteredFormat(backgroundColor,textFormat)"
            }
        }]}
    ).execute()

    _ID_FILE.write_text(sheet_id)
    log.info(f"Created conversation log sheet: {sheet_id}")
    return sheet_id


def log_conversation(
    user_msg: str,
    bot_reply: str,
    action: str = "",
    success: bool = True,
    notes: str = ""
):
    """שומר שיחה אחת לגיליון הלוג."""
    try:
        sheet_id = _get_or_create_sheet()
        svc      = _get_service()
        now      = datetime.now()
        row = [
            now.strftime("%d/%m/%Y"),
            now.strftime("%H:%M"),
            user_msg[:500],
            bot_reply[:1000],
            action,
            "✅" if success else "❌",
            notes
        ]
        svc.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range="שיחות!A:G",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]}
        ).execute()
    except Exception as e:
        log.error(f"conversation_log error: {e}")


def get_sheet_url() -> str:
    try:
        sheet_id = _get_or_create_sheet()
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
    except Exception:
        return ""


def get_recent(n: int = 100) -> list[dict]:
    """מחזיר N השיחות האחרונות לניתוח."""
    try:
        sheet_id = _get_or_create_sheet()
        svc = _get_service()
        result = svc.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range="שיחות!A2:G2000"
        ).execute()
        rows = result.get("values", [])
        recent = rows[-n:] if len(rows) > n else rows
        return [
            {
                "date":      r[0] if len(r) > 0 else "",
                "time":      r[1] if len(r) > 1 else "",
                "user_msg":  r[2] if len(r) > 2 else "",
                "bot_reply": r[3] if len(r) > 3 else "",
                "action":    r[4] if len(r) > 4 else "",
                "success":   r[5] if len(r) > 5 else "",
                "notes":     r[6] if len(r) > 6 else "",
            }
            for r in recent
        ]
    except Exception as e:
        log.error(f"get_recent error: {e}")
        return []
