"""
contacts.py — ספריית אנשי קשר: הורים, ספורטאים, טלפונים.
מבסס על 4 קבצי CSV (סירקין, נווה ירק, חגור, אהרונוביץ).
מאפשר הצלבה עם גיליונות נוכחות לזיהוי הורה מתוך שם ספורטאי.
"""

import csv, re, os, json
from pathlib import Path
from typing import Optional

# נתיבי קבצי אנשי קשר
_BASE = Path(__file__).parent
CONTACT_FILES = {
    "סירקין":    _BASE / "contacts.csv",
    "נווה ירק":  _BASE / "contacts (1).csv",
    "חגור":      _BASE / "contacts (2).csv",
    "אהרונוביץ": _BASE / "contacts (3).csv",
}

# cache שנטען פעם אחת
_cache: dict[str, list[dict]] = {}


def _normalize_phone(phone: str) -> str:
    """Normalize Israeli phone to 05X-XXXXXXX style."""
    p = re.sub(r"[^\d+]", "", phone)
    if p.startswith("+972"):
        p = "0" + p[4:]
    elif p.startswith("972"):
        p = "0" + p[3:]
    # Remove duplicate leading zeros
    if p.startswith("00"):
        p = p[1:]
    return p


def _parse_file(branch: str) -> list[dict]:
    path = CONTACT_FILES.get(branch)
    if not path or not path.exists():
        return []
    contacts = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            first = (row.get("First Name") or "").strip()
            middle = (row.get("Middle Name") or "").strip()
            last = (row.get("Last Name") or "").strip()
            phone1 = (row.get("Phone 1 - Value") or "").strip()
            phone2 = (row.get("Phone 2 - Value") or "").strip()

            # Some contacts split name across fields
            if middle and not last:
                raw = f"{first} {middle}".strip()
            elif last:
                raw = f"{first} {middle} {last}".strip().replace("  ", " ")
            else:
                raw = first

            # Strip the branch name suffix from the raw contact string
            for b in ["סירקין", "נווה ירק", "חגור", "אהרונוביץ", "אהרונוביץ'"]:
                raw = raw.replace(b, "").strip()
            raw = re.sub(r"\s{2,}", " ", raw)

            phones = []
            for p in [phone1, phone2]:
                if p and ":::" in p:
                    for part in p.split(":::"):
                        np = _normalize_phone(part.strip())
                        if np and len(np) >= 9:
                            phones.append(np)
                elif p:
                    np = _normalize_phone(p)
                    if np and len(np) >= 9:
                        phones.append(np)

            if not phones:
                continue

            contacts.append({
                "branch": branch,
                "raw": raw,
                "phone": phones[0],
                "phones": phones,
            })
    return contacts


def _load(branch: str) -> list[dict]:
    if branch not in _cache:
        _cache[branch] = _parse_file(branch)
    return _cache[branch]


def _load_all() -> list[dict]:
    result = []
    for b in CONTACT_FILES:
        result.extend(_load(b))
    return result


def _heb_words(s: str) -> list[str]:
    return re.findall(r"[א-ת]+", s)


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def find_parent(athlete_name: str, branch: Optional[str] = None) -> list[dict]:
    """
    חפש הורה לפי שם ספורטאי.
    מחזיר רשימת התאמות: [{"parent_name", "athlete_name", "phone", "branch"}]
    """
    athlete_words = _heb_words(athlete_name)
    if not athlete_words:
        return []

    results = []
    branches = [branch] if branch else list(CONTACT_FILES.keys())

    for b in branches:
        for c in _load(b):
            raw_words = _heb_words(c["raw"])
            # Check if any athlete word appears in the contact's raw string
            matches = sum(1 for w in athlete_words if w in raw_words)
            if matches >= len(athlete_words):
                # Try to extract parent name: words that are NOT the athlete name
                parent_words = [w for w in raw_words if w not in athlete_words]
                parent_name = " ".join(parent_words[:3]) if parent_words else c["raw"]
                results.append({
                    "parent_name": parent_name,
                    "athlete_name": athlete_name,
                    "phone": c["phone"],
                    "phones": c["phones"],
                    "branch": b,
                    "raw": c["raw"],
                })

    # Sort by match quality (more matched words = better)
    results.sort(key=lambda x: -sum(1 for w in athlete_words if w in _heb_words(x["raw"])))
    return results


def get_branch_contacts(branch: str) -> list[dict]:
    """Return all contacts for a branch."""
    return _load(branch)


def find_by_phone(phone: str) -> list[dict]:
    """Find contact by phone number."""
    normalized = _normalize_phone(phone)
    results = []
    for c in _load_all():
        if any(_normalize_phone(p) == normalized for p in c["phones"]):
            results.append(c)
    return results


def all_athlete_contacts(branch: str, athletes: list[tuple[int, str]]) -> list[dict]:
    """
    Given list of (row, name) athletes from attendance sheet,
    return list with parent contact for each.
    """
    result = []
    for row, name in athletes:
        parents = find_parent(name, branch)
        result.append({
            "athlete": name,
            "row": row,
            "parent": parents[0] if parents else None,
        })
    return result


def compose_absence_message(athlete_name: str, branch: str, date: str,
                             consecutive: int = 1) -> str:
    """Compose a WhatsApp message for an absent athlete's parent."""
    parents = find_parent(athlete_name, branch)
    if not parents:
        return f"לא נמצא איש קשר עבור {athlete_name}"

    parent = parents[0]["parent_name"]
    phone = parents[0]["phone"]

    if consecutive >= 3:
        msg = (f"שלום {parent},\n"
               f"שמתי לב ש{athlete_name} לא הגיע/ה ל-{consecutive} אימונים ברצף.\n"
               f"הכל בסדר? אשמח לדעת.")
    elif consecutive == 1:
        msg = (f"שלום {parent},\n"
               f"{athlete_name} לא הגיע/ה לאימון היום ({date}).\n"
               f"אם יש משהו, אני כאן.")
    else:
        msg = (f"שלום {parent},\n"
               f"{athlete_name} נעדר/ת {consecutive} אימונים לאחרונה. הכל טוב?")

    return f"📱 *{parent}* — {phone}\n\n{msg}"


def compose_payment_reminder(athlete_name: str, branch: str,
                              month: str, amount: Optional[int] = None) -> str:
    """Compose a payment reminder message."""
    parents = find_parent(athlete_name, branch)
    if not parents:
        return f"לא נמצא איש קשר עבור {athlete_name}"

    parent = parents[0]["parent_name"]
    phone = parents[0]["phone"]
    amount_str = f" ({amount}₪)" if amount else ""

    msg = (f"שלום {parent},\n"
           f"תזכורת לתשלום דמי אימון של {athlete_name}\n"
           f"לחודש {month}{amount_str}.\n"
           f"תודה! 🥋")

    return f"📱 *{parent}* — {phone}\n\n{msg}"


def stats() -> dict:
    """Return summary stats of contacts database."""
    total = 0
    by_branch = {}
    for b in CONTACT_FILES:
        c = _load(b)
        by_branch[b] = len(c)
        total += len(c)
    return {"total": total, "by_branch": by_branch}


def reload():
    """Force reload all contacts from CSV files."""
    _cache.clear()


def _parse_contact_birthday(bd_str: str):
    """Parse birthday string from Google Contacts CSV (format: YYYY-MM-DD or --MM-DD)."""
    import re
    if not bd_str:
        return None
    # Full date: 1984-01-03
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', bd_str)
    if m:
        return int(m.group(2)), int(m.group(3))  # (month, day)
    # No year: --01-03
    m = re.match(r'--(\d{2})-(\d{2})', bd_str)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def birthdays_this_week() -> list[dict]:
    """Return contacts whose birthday falls within the next 7 days."""
    from datetime import date, timedelta
    today = date.today()
    days_ahead = [(today + timedelta(days=i)) for i in range(7)]
    target_md = {(d.month, d.day) for d in days_ahead}

    results = []
    for branch, path in CONTACT_FILES.items():
        if not path.exists():
            continue
        import csv as _csv
        with open(path, encoding='utf-8') as f:
            reader = _csv.DictReader(f)
            for row in reader:
                bd = _parse_contact_birthday((row.get('Birthday') or '').strip())
                if not bd:
                    continue
                if bd in target_md:
                    first = (row.get('First Name') or '').strip()
                    phone = _normalize_phone((row.get('Phone 1 - Value') or '').strip())
                    # Remove branch name suffix from contact name
                    for b in ["סירקין", "נווה ירק", "חגור", "אהרונוביץ", "אהרונוביץ'"]:
                        first = first.replace(b, '').strip()
                    bday_date = date(today.year, bd[0], bd[1])
                    if bday_date < today:
                        bday_date = date(today.year + 1, bd[0], bd[1])
                    results.append({
                        'name': first,
                        'phone': phone,
                        'branch': branch,
                        'date': bday_date,
                        'day_str': f"{bd[1]}/{bd[0]}",
                    })
    results.sort(key=lambda x: x['date'])
    return results


def get_parent_for_student(athlete_name: str, branch: str = None) -> dict:
    """Return best parent match for athlete, or empty dict."""
    parents = find_parent(athlete_name, branch)
    return parents[0] if parents else {}


# ──────────────────────────────────────────────
# Verified cache — contacts matched to real students
# ──────────────────────────────────────────────

_VERIFIED_FILE: Path | None = None


def set_data_dir(data_dir: Path):
    global _VERIFIED_FILE
    _VERIFIED_FILE = Path(data_dir) / "contacts_verified.json"


def load_verified_cache() -> dict:
    if _VERIFIED_FILE and _VERIFIED_FILE.exists():
        try:
            return json.loads(_VERIFIED_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_verified_cache(data: dict):
    if _VERIFIED_FILE:
        _VERIFIED_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def import_and_verify(branch_students: dict) -> dict:
    """
    Cross-reference CSV contacts with known student names from sheets.
    branch_students: {branch: [student_name, ...]}
    Saves result to verified cache and returns summary.
    """
    _cache.clear()  # force reload from CSV files

    result: dict[str, dict] = {}
    verified = unverified = 0

    for branch in CONTACT_FILES:
        students = branch_students.get(branch, [])
        # (student_name, frozenset_of_heb_words)
        student_index = [(s, frozenset(_heb_words(s))) for s in students if _heb_words(s)]

        for c in _load(branch):
            raw_words = set(_heb_words(c["raw"]))
            matched_athlete = None

            for sname, swords in student_index:
                if len(swords) >= 2 and swords.issubset(raw_words):
                    matched_athlete = sname
                    break

            phone = c["phone"]
            if matched_athlete:
                aw = set(_heb_words(matched_athlete))
                parent_words = [w for w in _heb_words(c["raw"]) if w not in aw]
                parent_name = " ".join(parent_words[:3]) or c["raw"]
                result[phone] = {
                    "parent_name": parent_name,
                    "athlete_name": matched_athlete,
                    "branch": branch,
                    "verified": True,
                }
                verified += 1
            else:
                result[phone] = {
                    "parent_name": c["raw"],
                    "athlete_name": None,
                    "branch": branch,
                    "verified": False,
                }
                unverified += 1

    save_verified_cache(result)
    return {"total": verified + unverified, "verified": verified, "unverified": unverified}


def search_contacts(query: str, branch: str = None) -> list[dict]:
    """
    Search contacts by athlete or parent name.
    Checks verified cache first (better name split), then raw CSV.
    Returns up to 8 matches: [{parent_name, athlete_name, phone, branch, verified}]
    """
    qwords = _heb_words(query)
    if not qwords:
        return []

    cache = load_verified_cache()
    seen = set()
    scored: list[tuple[int, dict]] = []

    # Verified cache — clean parent/athlete split
    for phone, info in cache.items():
        aw = _heb_words(info.get("athlete_name") or "")
        pw = _heb_words(info.get("parent_name") or "")
        score = sum(1 for w in qwords if w in aw + pw)
        if score and (branch is None or info["branch"] == branch):
            scored.append((score, {
                "parent_name": info.get("parent_name") or "",
                "athlete_name": info.get("athlete_name"),
                "phone": phone,
                "branch": info["branch"],
                "verified": info.get("verified", False),
            }))
            seen.add(phone)

    # Raw CSV fallback for contacts not in cache
    for c in _load_all():
        if c["phone"] in seen:
            continue
        if branch and c["branch"] != branch:
            continue
        score = sum(1 for w in qwords if w in _heb_words(c["raw"]))
        if score:
            scored.append((score, {
                "parent_name": c["raw"],
                "athlete_name": None,
                "phone": c["phone"],
                "branch": c["branch"],
                "verified": False,
            }))

    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored[:8]]
