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
            "branch": "חגור",
            "tab":    "חגור",
            "groups": [
                {"name": "ד-ח",  "time": "15:15-16:30"},
                {"name": "א-ג",  "time": "16:30-17:15"},
                {"name": "גנים", "time": "17:15-18:00"},
            ],
        },
        {
            "branch": "נווה ירק",
            "tab":    "נווה ירק",
            "groups": [
                {"name": "ג-ו",  "time": "16:45-17:45"},
                {"name": "א-ב",  "time": "17:45-18:30"},
            ],
        },
    ],
    0: [  # שני — Monday
        {
            "branch": "סירקין",
            "tab":    "סירקין",
            "groups": [
                {"name": "ד-ו",       "time": "14:30-15:30"},
                {"name": "ג",         "time": "15:30-16:30"},
                {"name": "א-ב",       "time": "16:30-17:15"},
                {"name": "ז- בוגרים", "time": "18:00-19:30"},
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
                {"name": "נבחרת", "time": "13:15-15:00"},
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


def branches_for_date(d) -> list[str]:
    """Return list of branch names training on a given date object."""
    return [s["branch"] for s in SCHEDULE.get(d.weekday(), [])]


def groups_for_branch_on_date(branch: str, d) -> list[dict]:
    """Return list of {name, time} for a branch on a given date."""
    for s in SCHEDULE.get(d.weekday(), []):
        if s["branch"] == branch:
            return s["groups"]
    return []


def next_training_dates(branch: str, n: int = 5) -> list:
    """
    Return next n dates (as date objects) when branch has training.
    Skips Saturday. Looks up to 60 days ahead.
    """
    from datetime import date, timedelta
    results = []
    d = date.today()
    for _ in range(60):
        if d.weekday() != 5 and branch in branches_for_date(d):  # 5 = Saturday
            results.append(d)
            if len(results) == n:
                break
        d += timedelta(days=1)
    return results


def today_branches() -> list[str]:
    from datetime import date
    return branches_for_date(date.today())


def today_groups_for_branch(branch: str) -> list[dict]:
    from datetime import date
    return groups_for_branch_on_date(branch, date.today())
