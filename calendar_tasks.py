"""
Google Calendar task manager for Wolves Judo bot.
Handles natural-language task creation across the user's calendars.
"""

import os
import base64
import pickle
import warnings
from datetime import datetime, timedelta, date
from pathlib import Path
import json
import re

RECENT_EVENTS_FILE = Path("recent_calendar_events.json")

warnings.filterwarnings("ignore")
import googleapiclient.discovery

# All user calendars — name → calendar ID
CALENDARS = {
    "ג'ודו משימות":        "8rdpp62g6ufcjrh5cmfus41l4k@group.calendar.google.com",
    "אימוני מועדון הג'ודו": "kaaqsk472c6rp1gss319qscvm0@group.calendar.google.com",
    "אימונים טופז":         "300e5npvj9576rq638hoh3f7co@group.calendar.google.com",
    "חליפות ג'ודו":         "j040nkmgkoebugf99te0f3lqig@group.calendar.google.com",
    "טקסי מעבר חגורה":     "3b6975eud45u89jp5khish2124@group.calendar.google.com",
    "קורס מאמנים":          "tca60i9to8e0pqospm5r5o3uc8@group.calendar.google.com",
    "פגישות":               "p0q85imabp45mt1sq9l08hg79c@group.calendar.google.com",
    "משימות אישיות":        "family05439189821712760801@group.calendar.google.com",
    "תזכורות":              "s18v501o5mfm60s24gj6hukk0g@group.calendar.google.com",
    "התראות חשובות":        "topaz1993@gmail.com",
    "בייבי גרוט משימות":   "k78et1a01710sbje47ihjr0060@group.calendar.google.com",
    "נדל\"ן":               "hkmcruqi8qncuk1einkmgjqgao@group.calendar.google.com",
    "מניות":                "8uq6a9ftcc9rjgql9uk7ldfnqg@group.calendar.google.com",
    "העברת כספים":          "60vnu9l9qvign992qp2qhrb5ds@group.calendar.google.com",
    "רואה חשבון":           "f2ios8110gomgi0op1p5bf655o@group.calendar.google.com",
    "אינסטגרם":             "8l4tqlm2goufumv70lrskmpcn0@group.calendar.google.com",
}

# Emoji per calendar for display
CALENDAR_EMOJI = {
    "ג'ודו משימות":        "🥋",
    "אימוני מועדון הג'ודו": "🏋️",
    "אימונים טופז":         "💪",
    "חליפות ג'ודו":         "👘",
    "טקסי מעבר חגורה":     "🎌",
    "קורס מאמנים":          "📚",
    "פגישות":               "🤝",
    "משימות אישיות":        "✅",
    "תזכורות":              "🔔",
    "התראות חשובות":        "🚨",
    "בייבי גרוט משימות":   "👶",
    "נדל\"ן":               "🏠",
    "מניות":                "📈",
    "העברת כספים":          "💸",
    "רואה חשבון":           "📊",
    "אינסטגרם":             "📸",
}


def _get_service():
    b64 = os.environ.get("GOOGLE_CREDS_B64")
    if b64:
        creds = pickle.loads(base64.b64decode(b64))
    else:
        with open(os.path.expanduser("~/.wolves_judo_token.pickle"), "rb") as f:
            creds = pickle.load(f)
    return googleapiclient.discovery.build("calendar", "v3", credentials=creds)


def parse_date_hebrew(text: str) -> tuple:
    """
    Parse Hebrew date expressions from text.
    Returns (date_obj, time_str_or_None).
    Examples: "מחר", "מחר ב10:00", "25/6", "25/6/2026", "ביום שישי"
    """
    today = datetime.now().date()
    text = text.strip()

    # Time extraction
    time_match = re.search(r'(\d{1,2}):(\d{2})', text)
    time_str = f"{int(time_match.group(1)):02d}:{time_match.group(2)}" if time_match else None

    # Relative days
    if "מחר" in text:
        return today + timedelta(days=1), time_str
    if "היום" in text:
        return today, time_str
    if "מחרתיים" in text:
        return today + timedelta(days=2), time_str

    # Day of week
    day_map = {
        "ראשון": 6, "שני": 0, "שלישי": 1, "רביעי": 2,
        "חמישי": 3, "שישי": 4, "שבת": 5
    }
    for day_name, weekday in day_map.items():
        if day_name in text:
            days_ahead = (weekday - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return today + timedelta(days=days_ahead), time_str

    # DD/MM or DD/MM/YYYY
    date_match = re.search(r'(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?', text)
    if date_match:
        d = int(date_match.group(1))
        m = int(date_match.group(2))
        y = int(date_match.group(3)) if date_match.group(3) else today.year
        if y < 100:
            y += 2000
        try:
            return date(y, m, d), time_str
        except ValueError:
            pass

    return None, time_str


def find_calendar(text: str) -> tuple:
    """
    Find best matching calendar name from text.
    Returns (calendar_name, calendar_id) or (None, None).
    """
    text_lower = text.lower()
    for name in CALENDARS:
        if name.lower() in text_lower or any(w in text_lower for w in name.lower().split()):
            return name, CALENDARS[name]
    return None, None


def add_event(calendar_name: str, title: str, event_date: date,
              time_str=None, description: str = "") -> str:
    """
    Create a calendar event.
    Returns link to the event.
    """
    service = _get_service()
    calendar_id = CALENDARS.get(calendar_name)
    if not calendar_id:
        raise ValueError(f"לא נמצא יומן: {calendar_name}")

    if time_str:
        hour, minute = map(int, time_str.split(":"))
        start_dt = datetime.combine(event_date, datetime.min.time()).replace(hour=hour, minute=minute)
        end_dt = start_dt + timedelta(minutes=10)
        event = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Jerusalem"},
            "end":   {"dateTime": end_dt.isoformat(),   "timeZone": "Asia/Jerusalem"},
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 10}]},
        }
    else:
        event = {
            "summary": title,
            "description": description,
            "start": {"date": event_date.isoformat()},
            "end":   {"date": (event_date + timedelta(days=1)).isoformat()},
        }

    result = service.events().insert(calendarId=calendar_id, body=event).execute()
    event_id = result.get("id", "")
    link = result.get("htmlLink", "")

    # Save to recent events log (keep last 10)
    log = _load_recent()
    log.append({
        "id": event_id,
        "calendar_name": calendar_name,
        "calendar_id": calendar_id,
        "title": title,
        "date": event_date.isoformat(),
        "time": time_str,
        "created": datetime.now().isoformat(),
    })
    _save_recent(log[-10:])

    return link


def _load_recent() -> list:
    if RECENT_EVENTS_FILE.exists():
        return json.loads(RECENT_EVENTS_FILE.read_text(encoding="utf-8"))
    return []


def _save_recent(log: list):
    RECENT_EVENTS_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def get_recent_events(n: int = 3) -> list:
    """Return last n events added by the bot."""
    return _load_recent()[-n:]


def delete_event(event_index: int) -> str:
    """
    Delete event by index in recent list (0=oldest of last 3, 2=newest).
    Returns title of deleted event.
    """
    log = _load_recent()
    recent = log[-3:]
    if event_index < 0 or event_index >= len(recent):
        raise ValueError("אינדקס לא תקין")

    event = recent[event_index]
    service = _get_service()
    service.events().delete(
        calendarId=event["calendar_id"],
        eventId=event["id"]
    ).execute()

    # Remove from log
    full_idx = len(log) - len(recent) + event_index
    log.pop(full_idx)
    _save_recent(log)

    return event["title"]


def calendar_list_display() -> str:
    """Return formatted list of all calendars for display."""
    lines = []
    for name, _ in CALENDARS.items():
        emoji = CALENDAR_EMOJI.get(name, "📅")
        lines.append(f"{emoji} {name}")
    return "\n".join(lines)
