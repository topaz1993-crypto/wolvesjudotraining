"""
Tracks consecutive absences per student across all branches.
Creates a Google Calendar reminder after 3 consecutive absences.
"""

import json
import pickle
import base64
import os
from datetime import datetime, timedelta
from pathlib import Path

import googleapiclient.discovery

ABSENCE_FILE = Path("absence_log.json")
JUDO_TASKS_CALENDAR_ID = "8rdpp62g6ufcjrh5cmfus41l4k@group.calendar.google.com"


def set_data_dir(data_dir: Path):
    global ABSENCE_FILE
    ABSENCE_FILE = Path(data_dir) / "absence_log.json"


def _get_calendar_service():
    b64 = os.environ.get("GOOGLE_CREDS_B64")
    if b64:
        creds = pickle.loads(base64.b64decode(b64))
    else:
        with open(os.path.expanduser("~/.wolves_judo_token.pickle"), "rb") as f:
            creds = pickle.load(f)
    return googleapiclient.discovery.build("calendar", "v3", credentials=creds)


def load_log() -> dict:
    if ABSENCE_FILE.exists():
        return json.loads(ABSENCE_FILE.read_text(encoding="utf-8"))
    return {}


def save_log(log: dict):
    ABSENCE_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_name(name: str) -> str:
    return " ".join(name.strip().split())


def record_attendance(students: list[tuple], absent_indices: set[int], date_str: str, branch: str, group: str):
    """
    Update absence log after an attendance session.
    Returns list of student names who hit 3 consecutive absences.
    """
    log = load_log()
    alert_students = []

    for i, (_, name) in enumerate(students, start=1):
        key = normalize_name(name)
        if key not in log:
            log[key] = []

        entry = {
            "date": date_str,
            "branch": branch,
            "group": group,
            "absent": i in absent_indices,
        }
        log[key].append(entry)

        # Keep only last 10 entries per student
        log[key] = log[key][-10:]

        # Check for 3 consecutive absences (across all branches)
        consecutive = 0
        for e in reversed(log[key]):
            if e["absent"]:
                consecutive += 1
            else:
                break

        if consecutive == 3:
            alert_students.append(name)

    save_log(log)
    return alert_students


def remove_student(name: str):
    """Remove a student from the absence log when they drop out."""
    log = load_log()
    key = normalize_name(name)
    if key in log:
        del log[key]
        save_log(log)


def get_absence_streak(name: str) -> int:
    """Return current consecutive absence streak for a student."""
    log = load_log()
    key = normalize_name(name)
    entries = log.get(key, [])
    streak = 0
    for e in reversed(entries):
        if e["absent"]:
            streak += 1
        else:
            break
    return streak


def create_calendar_reminder(student_name: str, branch: str, group: str, training_date: str):
    """Create a Google Calendar event for the day after the 3rd absence."""
    try:
        service = _get_calendar_service()

        # Parse training date and set reminder for next day at 9:00
        training_dt = datetime.strptime(training_date, "%d/%m/%Y")
        reminder_dt = training_dt + timedelta(days=1)
        start = reminder_dt.replace(hour=9, minute=0, second=0)
        end = start + timedelta(hours=1)

        event = {
            "summary": f"📞 ליצור קשר — {student_name} ({branch} {group})",
            "description": (
                f"הספורטאי {student_name} לא הגיע ל-3 אימונים ברצף.\n"
                f"סניף: {branch} | קבוצה: {group}\n"
                f"תאריך האימון האחרון: {training_date}\n\n"
                f"יש לבדוק מה קרה ולעדכן תשלום במידת הצורך."
            ),
            "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Jerusalem"},
            "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Jerusalem"},
            "colorId": "11",  # אדום
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": 30}],
            },
        }

        result = service.events().insert(calendarId=JUDO_TASKS_CALENDAR_ID, body=event).execute()
        return result.get("htmlLink", "")
    except Exception as e:
        return f"error: {e}"


def create_dropout_reminder(student_name: str, branch: str, group: str, dropout_date: str):
    """Create a calendar reminder to cancel registration after a dropout.
    Returns (event_id, link) or (None, error_str)."""
    try:
        service = _get_calendar_service()
        dt = datetime.strptime(dropout_date, "%d/%m/%Y")
        reminder_dt = dt + timedelta(days=1)
        start = reminder_dt.replace(hour=9, minute=0, second=0)
        end = start + timedelta(minutes=10)

        event = {
            "summary": f"🖤 לבטל רישום — {student_name} ({branch} {group})",
            "description": (
                f"הספורטאי {student_name} פרש מהמועדון.\n"
                f"סניף: {branch} | קבוצה: {group}\n"
                f"תאריך פרישה: {dropout_date}\n\n"
                f"יש לבטל את הרישום ולעצור חיוב תשלום."
            ),
            "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Jerusalem"},
            "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Jerusalem"},
            "colorId": "8",
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 30}]},
        }

        result = service.events().insert(calendarId=JUDO_TASKS_CALENDAR_ID, body=event).execute()
        return result.get("id"), result.get("htmlLink", "")
    except Exception as e:
        return None, f"error: {e}"


def delete_calendar_event(event_id: str):
    """Delete a calendar event by ID from ג'ודו משימות."""
    try:
        service = _get_calendar_service()
        service.events().delete(calendarId=JUDO_TASKS_CALENDAR_ID, eventId=event_id).execute()
    except Exception:
        pass


def create_new_student_reminder(student_name: str, branch: str, group: str, join_date: str):
    """Create a calendar reminder to follow up with new student's parents."""
    try:
        service = _get_calendar_service()
        dt = datetime.strptime(join_date, "%d/%m/%Y")
        reminder_dt = dt + timedelta(days=1)
        start = reminder_dt.replace(hour=9, minute=0, second=0)
        end = start + timedelta(minutes=10)

        event = {
            "summary": f"🟢 לברר עם הורים — {student_name} ({branch} {group})",
            "description": (
                f"הספורטאי {student_name} הצטרף לראשונה.\n"
                f"סניף: {branch} | קבוצה: {group}\n"
                f"תאריך האימון הראשון: {join_date}\n\n"
                f"יש לדבר עם ההורים — לברר איך היה, לשאול אם ממשיכים, ולעדכן רישום רשמי."
            ),
            "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Jerusalem"},
            "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Jerusalem"},
            "colorId": "10",  # ירוק
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": 30}],
            },
        }

        result = service.events().insert(calendarId=JUDO_TASKS_CALENDAR_ID, body=event).execute()
        return result.get("htmlLink", "")
    except Exception as e:
        return f"error: {e}"
