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
