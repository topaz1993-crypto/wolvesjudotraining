"""
payment_matcher.py — התאמת רשומות invoice4u לתלמידים בגיליון.
שומר mapping קבוע ב-payment_mapping.json.

מפתח mapping: customer_id (ממספר לקוח ב-invoice4u)
ערך: {student_first, student_last, branch, sheet, row_idx}
"""

import json, re
from pathlib import Path
from typing import Optional
from difflib import SequenceMatcher

MAPPING_FILE = Path("payment_mapping.json")

# ── helpers ──────────────────────────────────────────────────────────────────

def _heb_words(s: str) -> list[str]:
    return re.findall(r'[א-ת]+', s)

def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def _last_word(name: str) -> str:
    words = _heb_words(name)
    return words[-1] if words else ''

def _first_word(name: str) -> str:
    words = _heb_words(name)
    return words[0] if words else ''

# ── mapping persistence ───────────────────────────────────────────────────────

def load_mapping() -> dict:
    """Load {customer_id: {student_first, student_last, branch, sheet, row_idx}}."""
    if MAPPING_FILE.exists():
        try:
            return json.loads(MAPPING_FILE.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}


def save_mapping(mapping: dict) -> None:
    MAPPING_FILE.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2), encoding='utf-8'
    )


def add_to_mapping(customer_id: str, customer_name: str,
                   student: dict, mapping: dict) -> dict:
    """Add or update one entry in mapping and return updated dict."""
    if not customer_id:
        # Use name as key when no ID
        customer_id = f"name:{customer_name}"
    mapping[customer_id] = {
        'student_first': student['first'],
        'student_last':  student['last'],
        'branch':        student.get('branch', ''),
        'sheet':         student.get('sheet', ''),
        'row_idx':       student.get('row_idx', 0),
        'customer_name': customer_name,
    }
    save_mapping(mapping)
    return mapping


# ── auto-matching logic ────────────────────────────────────────────────────────

def _find_by_child_and_last(child_first: str, parent_last: str,
                             sheet_students: list[dict]) -> list[dict]:
    """Find student by child first name + parent last name."""
    matches = []
    for s in sheet_students:
        if s['first'].strip() != child_first.strip():
            continue
        # Last name partial match
        sl = s['last'].strip()
        if parent_last and (parent_last in sl or sl in parent_last or
                            _similarity(parent_last, sl) > 0.75):
            matches.append(s)
        elif not parent_last:
            matches.append(s)
    return matches


def _find_by_fullname(name: str, sheet_students: list[dict]) -> list[dict]:
    """Match a name against first+last name of students."""
    words = _heb_words(name)
    if len(words) < 2:
        # Single name — search in first name only
        return [s for s in sheet_students if s['first'] == words[0]] if words else []

    first, last = words[0], words[-1]
    exact = [s for s in sheet_students if s['first'] == first and s['last'] == last]
    if exact:
        return exact
    # Last name only
    return [s for s in sheet_students if s['last'] == last]


def try_auto_match(record: dict,
                   sheet_students: list[dict]) -> Optional[dict]:
    """
    Try to automatically match one invoice record to a student row.
    Returns a student dict or None if ambiguous / not found.
    """
    children    = record.get('children', [])
    parent_name = record.get('parent_name', '')
    parent_last = _last_word(parent_name)

    # Strategy 1: child name in parens + parent last name
    if children:
        for child_first in children:
            m = _find_by_child_and_last(child_first, parent_last, sheet_students)
            if len(m) == 1:
                return m[0]
            if len(m) > 1:
                # Narrow by exact last name match
                exact = [s for s in m if s['last'] == parent_last]
                if len(exact) == 1:
                    return exact[0]

    # Strategy 2: no parens — parent name IS student name
    if not children:
        m = _find_by_fullname(parent_name, sheet_students)
        if len(m) == 1:
            return m[0]

    return None


# ── main matching function ────────────────────────────────────────────────────

def match_records(records: list[dict],
                  sheet_students: list[dict],
                  mapping: Optional[dict] = None) -> list[dict]:
    """
    Match a list of invoice4u records against all sheet students.

    Returns list of:
    {
        'record':       original record dict,
        'status':       'saved' | 'auto' | 'unknown',
        'student':      student dict or None,
        'mapping_key':  the key used in mapping (customer_id or name:xxx),
    }
    """
    if mapping is None:
        mapping = load_mapping()

    results = []
    for rec in records:
        cid  = rec.get('customer_id', '')
        cname = rec.get('customer_name', '')
        key  = cid if cid else f"name:{cname}"

        # ── Saved mapping ──────────────────────────────────────────────────
        if key in mapping:
            m = mapping[key]
            student = next(
                (s for s in sheet_students
                 if s['first'] == m.get('student_first')
                 and s['last'] == m.get('student_last')
                 and s.get('branch') == m.get('branch')),
                None
            )
            if student is None:
                # Row may have shifted — rebuild using names
                student = {
                    'first':   m['student_first'],
                    'last':    m['student_last'],
                    'branch':  m['branch'],
                    'sheet':   m['sheet'],
                    'row_idx': m['row_idx'],
                }
            results.append({
                'record':      rec,
                'status':      'saved',
                'student':     student,
                'mapping_key': key,
            })
            continue

        # ── Auto-match ─────────────────────────────────────────────────────
        auto = try_auto_match(rec, sheet_students)
        if auto:
            results.append({
                'record':      rec,
                'status':      'auto',
                'student':     auto,
                'mapping_key': key,
            })
        else:
            results.append({
                'record':      rec,
                'status':      'unknown',
                'student':     None,
                'mapping_key': key,
            })

    return results


def search_student(query: str, sheet_students: list[dict]) -> list[dict]:
    """
    Search for a student by any name fragment.
    Returns top matches for bot to present.
    """
    query_words = _heb_words(query)
    if not query_words:
        return []

    scored = []
    for s in sheet_students:
        name = f"{s['first']} {s['last']}"
        score = sum(1 for w in query_words
                    if w in s['first'] or w in s['last'])
        if score > 0:
            scored.append((score, s))

    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:8]]
