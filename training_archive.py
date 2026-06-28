"""
Training plan archive — saves every plan to a local JSON file and supports search.
"""

import json
from datetime import date, datetime
from pathlib import Path

ARCHIVE_FILE = Path("training_archive.json")


def _load() -> list:
    if ARCHIVE_FILE.exists():
        try:
            return json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save(records: list):
    ARCHIVE_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def save_plan(branch: str, tab: str, group: str, plan_date: str, content: dict):
    """
    Save a training plan to the archive.
    content = {row_type: text, ...}  e.g. {"חימום": "גאורגי", "תרגול": "אוצי קומי", ...}
    plan_date = "YYYY-MM-DD" or "DD/MM/YYYY"
    """
    # Normalize date to DD/MM/YYYY
    if "-" in plan_date and len(plan_date) == 10:
        d = date.fromisoformat(plan_date)
        date_display = f"{d.day}/{d.month}/{d.year}"
    else:
        date_display = plan_date

    records = _load()
    records.append({
        "branch":    branch,
        "tab":       tab,
        "group":     group,
        "date":      date_display,
        "saved_at":  datetime.now().isoformat(),
        "content":   content,
    })
    _save(records)


def search(query: str, limit: int = 5) -> list[dict]:
    """
    Simple search — returns records matching branch/group/date/content keywords.
    Returns most recent matches first.
    """
    records = _load()
    q = query.strip().lower()
    words = q.split()

    def score(r):
        text = " ".join([
            r.get("branch", ""),
            r.get("group", ""),
            r.get("date", ""),
            " ".join(r.get("content", {}).values()),
        ]).lower()
        return sum(1 for w in words if w in text)

    scored = [(score(r), r) for r in records]
    scored = [(s, r) for s, r in scored if s > 0]
    scored.sort(key=lambda x: (-x[0], x[1].get("saved_at", "")), reverse=False)
    # Sort: highest score first, then most recent
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:limit]]


def recent(branch: str = None, group: str = None, n: int = 5) -> list[dict]:
    """Return n most recent plans, optionally filtered by branch/group."""
    records = _load()
    if branch:
        records = [r for r in records if r.get("branch") == branch]
    if group:
        records = [r for r in records if r.get("group") == group]
    return records[-n:][::-1]


def format_plan(r: dict) -> str:
    """Format a single archive record as readable text."""
    lines = [f"📅 *{r['date']}* — {r['branch']} / {r['group']}"]
    for row_type, text in r.get("content", {}).items():
        if text:
            lines.append(f"  *{row_type}:* {text}")
    return "\n".join(lines)


def stats() -> str:
    """Return a short summary of the archive."""
    records = _load()
    if not records:
        return "הארכיון ריק."
    by_branch: dict[str, int] = {}
    for r in records:
        by_branch[r.get("branch", "?")] = by_branch.get(r.get("branch", "?"), 0) + 1
    lines = [f"📚 *ארכיון תוכניות* — {len(records)} סה\"כ\n"]
    for b, cnt in sorted(by_branch.items()):
        lines.append(f"  {b}: {cnt}")
    return "\n".join(lines)


def history_for_group(branch: str, group: str, n: int = 4) -> list[dict]:
    """Return last n plans for a specific branch+group, most recent first."""
    records = _load()
    filtered = [r for r in records
                if r.get("branch") == branch and r.get("group") == group]
    return filtered[-n:][::-1]


def format_history(branch: str, group: str, n: int = 3) -> str:
    """Format recent history for a group as readable text for Claude context."""
    records = history_for_group(branch, group, n)
    if not records:
        return f"אין היסטוריה עבור {branch} / {group}"
    lines = [f"📋 *{branch} — {group}* — {len(records)} אימונים אחרונים:\n"]
    for r in records:
        lines.append(f"*{r['date']}*")
        for row_type, val in r.get("content", {}).items():
            if val:
                lines.append(f"  {row_type}: {val}")
        lines.append("")
    return "\n".join(lines)


def what_was_used_recently(branch: str, group: str, row_type: str, n: int = 3) -> list[str]:
    """Return list of content used in a specific row_type for the last n sessions."""
    records = history_for_group(branch, group, n)
    return [r["content"].get(row_type, "") for r in records if r["content"].get(row_type)]


BRANCH_ROW_TYPES = {
    "איפון פייט": ["חימום", "תרגול", "קרבות", "משחק"],
    "פונקציונלי": ["חימום", "תרגול", "קרבות", "כוח"],
}
DEFAULT_ROW_TYPES = ["חימום", "תרגול", "קרבות", "משחק", "כוח", "נוסף"]

BRANCH_STYLE_NOTES = {
    "איפון פייט": (
        "⚠️ איפון פייט — לא ג'ודו טכני! "
        "חימום=משחק פעיל, תרגול=טבאטה ספוטיפיי, קרבות=משחק תחרותי (ג'ודופונג/עיר הקרח/ביסט גיימס), "
        "משחק=סיום קצר. ללא הפלות, ללא רנדורי."
    ),
    "פונקציונלי": (
        "⚠️ פונקציונלי — CrossFit, לא ג'ודו! "
        "חימום=Warm-up, תרגול=Strength (E2MOM/E3MOM עם משקולות), קרבות=Metcon (AMRAP/EMOM). "
        "כתוב באנגלית+עברית. ללא ג'ודו."
    ),
}


def suggest_context_for_claude(branch: str, groups: list) -> str:
    """
    Build a rich context string for Claude with:
    1. Last 6 sessions per group (what NOT to repeat)
    2. Full repertoire per row_type (all available options from history)
    """
    row_types = BRANCH_ROW_TYPES.get(branch, DEFAULT_ROW_TYPES)
    all_records = _load()
    style_note = BRANCH_STYLE_NOTES.get(branch, "")
    lines = [
        f"היסטוריית אימונים — {branch}",
    ]
    if style_note:
        lines.append(style_note)
    lines += [
        "השתמש בנתונים אלה כדי לבנות תוכנית מגוונת: אל תחזור על מה שנעשה ב-6 האימונים האחרונים.",
        "כתוב בסגנון של טופז — קצר, טכני, ישיר. ללא מספרים בתחילת שורה.\n"
    ]
    ROW_TYPES = row_types

    for group in groups:
        recs_all = [r for r in all_records
                    if r.get("branch") == branch and r.get("group") == group]
        if not recs_all:
            lines.append(f"**{group}**: אין היסטוריה")
            continue

        recent = recs_all[-6:][::-1]  # last 6, newest first
        lines.append(f"**{group}** ({len(recs_all)} אימונים בארכיון):")

        # Recent sessions
        lines.append("  6 אחרונים (אל תחזור על אלה):")
        for r in recent:
            row_summary = " | ".join(f"{k}: {v}" for k, v in r["content"].items() if v)
            lines.append(f"    {r['date']}: {row_summary}")

        # Full repertoire per row_type (all unique values ever used)
        lines.append("  רפרטואר מלא לפי קטגוריה:")
        for rt in ROW_TYPES:
            used_recently = {r["content"].get(rt, "") for r in recent}
            all_vals = list(dict.fromkeys(
                r["content"].get(rt, "") for r in recs_all
                if r["content"].get(rt)
            ))
            # Mark recently used
            display = []
            for v in all_vals:
                mark = " ⚠️(לאחרונה)" if v in used_recently and v else ""
                display.append(f"{v}{mark}")
            if display:
                lines.append(f"    {rt}: {' / '.join(display)}")
        lines.append("")

    return "\n".join(lines)
