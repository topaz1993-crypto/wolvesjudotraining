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


def set_data_dir(data_dir: Path):
    global RECENT_EVENTS_FILE
    RECENT_EVENTS_FILE = Path(data_dir) / "recent_calendar_events.json"

warnings.filterwarnings("ignore")
import googleapiclient.discovery

# All user calendars — name → calendar ID
CALENDARS = {
    "ג'ודו משימות":        "8rdpp62g6ufcjrh5cmfus41l4k@group.calendar.google.com",
    "אימוני מועדון הג'ודו": "kaaqsk472c6rp1gss319qscvm0@group.calendar.google.com",
    "אימונים טופז":         "300e5npvj9576rq638hoh3f7co@group.calendar.google.com",
    "חליפות ג'ודו":         "j040nkmgkoebugf99te0f3lqig@group.calendar.google.com",
    "טקסי מעבר חגורה":     "3b6975eud45u89jp5khish2124@group.calendar.google.com",
    "טקסי חגורה":          "3b6975eud45u89jp5khish2124@group.calendar.google.com",
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


def parse_date_range_hebrew(text: str) -> tuple[date, date]:
    """
    Parse a Hebrew date-range expression.
    Returns (date_from, date_to).
    Examples: "היום", "מחר", "השבוע", "שבוע הבא", "החודש", "יוני", "מ-25/6 עד 1/7"
    """
    today = datetime.now().date()
    t = text

    # Explicit range: מ-X עד Y
    range_match = re.search(r'מ[- ]?(\S+)\s+עד\s+(\S+)', t)
    if range_match:
        d1, _ = parse_date_hebrew(range_match.group(1))
        d2, _ = parse_date_hebrew(range_match.group(2))
        if d1 and d2:
            return (d1, d2) if d1 <= d2 else (d2, d1)

    # Month by name
    month_map = {
        "ינואר": 1, "פברואר": 2, "מרץ": 3, "אפריל": 4,
        "מאי": 5, "יוני": 6, "יולי": 7, "אוגוסט": 8,
        "ספטמבר": 9, "אוקטובר": 10, "נובמבר": 11, "דצמבר": 12,
    }
    for month_name, month_num in month_map.items():
        if month_name in t:
            year = today.year
            # if the month already passed, assume next year
            if month_num < today.month:
                year += 1
            first = date(year, month_num, 1)
            if month_num == 12:
                last = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                last = date(year, month_num + 1, 1) - timedelta(days=1)
            return first, last

    # Next week
    if "שבוע הבא" in t:
        days_to_sunday = (6 - today.weekday() + 1) % 7 or 7
        next_sun = today + timedelta(days=days_to_sunday)
        return next_sun, next_sun + timedelta(days=6)

    # This week (Sunday–Saturday)
    if "השבוע" in t or "שבוע" in t:
        days_since_sunday = (today.weekday() + 1) % 7
        week_start = today - timedelta(days=days_since_sunday)
        return week_start, week_start + timedelta(days=6)

    # Next month
    if "חודש הבא" in t:
        if today.month == 12:
            first = date(today.year + 1, 1, 1)
        else:
            first = date(today.year, today.month + 1, 1)
        if first.month == 12:
            last = date(first.year + 1, 1, 1) - timedelta(days=1)
        else:
            last = date(first.year, first.month + 1, 1) - timedelta(days=1)
        return first, last

    # This month
    if "החודש" in t or "חודש" in t:
        first = date(today.year, today.month, 1)
        if today.month == 12:
            last = date(today.year + 1, 1, 1) - timedelta(days=1)
        else:
            last = date(today.year, today.month + 1, 1) - timedelta(days=1)
        return first, last

    # Single day expressions
    d, _ = parse_date_hebrew(t)
    if d:
        return d, d

    # Default: today
    return today, today


def get_events_range(date_from: date, date_to: date) -> list:
    """
    Fetch all events from all calendars between date_from and date_to (inclusive).
    Returns list of dicts sorted by datetime.
    """
    service = _get_service()
    time_min = datetime.combine(date_from, datetime.min.time()).isoformat() + "Z"
    time_max = datetime.combine(date_to + timedelta(days=1), datetime.min.time()).isoformat() + "Z"

    all_events = []
    for cal_name, cal_id in CALENDARS.items():
        try:
            result = service.events().list(
                calendarId=cal_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            ).execute()
            for ev in result.get("items", []):
                start = ev.get("start", {})
                start_str = start.get("dateTime") or start.get("date", "")
                # Parse to sortable
                try:
                    if "T" in start_str:
                        # Parse with timezone awareness and convert to Israel time (UTC+3)
                        if start_str.endswith("Z"):
                            dt_utc = datetime.fromisoformat(start_str[:19])
                            dt = dt_utc + timedelta(hours=3)
                        elif "+" in start_str[10:] or (len(start_str) > 19 and start_str[19] == "-"):
                            # Has offset like +03:00 or -05:00 — parse offset manually
                            naive = datetime.fromisoformat(start_str[:19])
                            offset_str = start_str[19:]
                            sign = 1 if offset_str[0] == "+" else -1
                            parts = offset_str[1:].split(":")
                            offset_h = int(parts[0]) if parts else 0
                            offset_m = int(parts[1]) if len(parts) > 1 else 0
                            utc = naive - timedelta(hours=sign * offset_h, minutes=sign * offset_m)
                            dt = utc + timedelta(hours=3)  # to Israel
                        else:
                            dt = datetime.fromisoformat(start_str[:19])
                        time_display = dt.strftime("%H:%M")
                        date_display = dt.strftime("%d/%m")
                        sort_key = dt
                    else:
                        d = datetime.strptime(start_str[:10], "%Y-%m-%d").date()
                        time_display = ""
                        date_display = d.strftime("%d/%m")
                        sort_key = datetime.combine(d, datetime.min.time())
                except Exception:
                    time_display = ""
                    date_display = start_str[:10]
                    sort_key = datetime.min

                all_events.append({
                    "calendar": cal_name,
                    "emoji": CALENDAR_EMOJI.get(cal_name, "📅"),
                    "title": ev.get("summary", "(ללא כותרת)"),
                    "date": date_display,
                    "time": time_display,
                    "sort_key": sort_key,
                    "description": ev.get("description", ""),
                })
        except Exception:
            continue

    all_events.sort(key=lambda x: x["sort_key"])
    return all_events


def format_events_for_claude(events: list, date_from: date, date_to: date) -> str:
    """Format events list as context string for Claude."""
    if not events:
        return "אין אירועים בטווח זה."

    today = datetime.now().date()

    DAY_HE = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]

    def label(d: date) -> str:
        day_name = DAY_HE[d.weekday()]
        date_str = d.strftime('%d/%m/%Y')
        if d == today:
            return f"היום — יום {day_name} {date_str}"
        if d == today + timedelta(days=1):
            return f"מחר — יום {day_name} {date_str}"
        return f"יום {day_name} {date_str}"

    by_date: dict[str, list] = {}
    for ev in events:
        key = ev["date"]
        by_date.setdefault(key, []).append(ev)

    lines = [f"אירועים {date_from.strftime('%d/%m')}–{date_to.strftime('%d/%m')}:\n"]
    for date_key, evs in by_date.items():
        try:
            d = datetime.strptime(date_key, "%d/%m").date().replace(year=date_from.year)
        except Exception:
            d = date_from
        lines.append(f"📆 {label(d)}:")
        for ev in evs:
            time_part = f" {ev['time']}" if ev["time"] else ""
            lines.append(f"  {ev['emoji']}{time_part} {ev['title']} [{ev['calendar']}]")
    return "\n".join(lines)
