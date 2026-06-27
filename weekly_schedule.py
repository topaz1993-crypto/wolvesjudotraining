"""
Weekly training schedule for Wolves Judo.
Used to auto-detect which branch/groups train on the current day.
weekday(): Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4, Saturday=5, Sunday=6
"""

# DAY_HE: correct Hebrew day names by Python weekday index
DAY_HE = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]

# SCHEDULE: weekday → list of {branch, tab, groups: [{name, time}]}
SCHEDULE = {
    6: [  # ראשון — Sunday
        {
            "branch": "נווה ירק",
            "tab":    "נווה ירק",
            "groups": [
                {"name": "ז-בוגרים", "time": "15:15-16:45"},
                {"name": "ג-ו",      "time": "16:45-17:45"},
                {"name": "א-ב",      "time": "17:45-18:30"},
            ],
        },
    ],
    0: [  # שני — Monday
        {
            "branch": "סירקין",
            "tab":    "סירקין",
            "groups": [
                {"name": "ד-ו",           "time": "14:30-15:30"},
                {"name": "ג",             "time": "15:30-16:30"},
                {"name": "א-ב",           "time": "16:30-17:15"},
                {"name": "גנים - חמישי",  "time": "17:15-18:00"},
                {"name": "ז- בוגרים",     "time": "18:00-19:30"},
            ],
        },
    ],
    1: [  # שלישי — Tuesday
        {
            "branch": "נווה ירק",
            "tab":    "נווה ירק",
            "groups": [
                {"name": "גנים", "time": "16:00-16:45"},
                {"name": "ג-ו",  "time": "16:45-17:45"},
                {"name": "א-ב",  "time": "17:45-18:30"},
            ],
        },
    ],
    2: [  # רביעי — Wednesday
        {
            "branch": "אהרונוביץ",
            "tab":    "אהרונוביץ",
            "groups": [
                {"name": "א-ו", "time": "13:50-14:50"},
            ],
        },
        {
            "branch": "פונקציונלי",
            "tab":    "פונקציונאלי ",
            "groups": [
                {"name": 'ז-ח',   "time": "16:15-17:15"},
                {"name": 'ט-י"ב', "time": "17:15-18:15"},
            ],
        },
        {
            "branch": "איפון פייט",
            "tab":    "איפון פייט",
            "groups": [
                {"name": "ב-ד", "time": "18:30-19:15"},
                {"name": "ה-ז", "time": "19:15-20:00"},
            ],
        },
    ],
    3: [  # חמישי — Thursday
        {
            "branch": "סירקין",
            "tab":    "סירקין",
            "groups": [
                {"name": "ד-ו",           "time": "14:30-15:30"},
                {"name": "ג",             "time": "15:30-16:30"},
                {"name": "א-ב",           "time": "16:30-17:15"},
                {"name": "גנים - חמישי",  "time": "17:15-18:00"},
                {"name": "ז- בוגרים",     "time": "18:00-19:30"},
            ],
        },
    ],
    4: [  # שישי — Friday
        {
            "branch": "פונקציונלי",
            "tab":    "פונקציונאלי ",
            "groups": [
                {"name": 'ז-ח',   "time": "08:00-09:00"},
                {"name": 'ט-י"ב', "time": "09:00-10:00"},
            ],
        },
        {
            "branch": "נבחרת",
            "tab":    "נבחרת",
            "groups": [
                {"name": "נבחרת",    "time": "13:15-15:00"},
                {"name": "ט ומעלה", "time": "15:30-17:45"},
            ],
        },
    ],
    5: [  # שבת — Saturday
        # אין אימונים
    ],
}


def today_schedule() -> list[dict]:
    """Return today's training schedule."""
    from datetime import date
    return SCHEDULE.get(date.today().weekday(), [])


def today_name() -> str:
    """Return today's Hebrew day name."""
    from datetime import date
    return DAY_HE[date.today().weekday()]


def day_name(d) -> str:
    """Return Hebrew day name for a date object."""
    return DAY_HE[d.weekday()]


def today_branches() -> list[str]:
    """Return list of branch names training today."""
    return [s["branch"] for s in today_schedule()]


def today_groups_for_branch(branch: str) -> list[dict]:
    """Return list of {name, time} for a branch today."""
    for s in today_schedule():
        if s["branch"] == branch:
            return s["groups"]
    return []
