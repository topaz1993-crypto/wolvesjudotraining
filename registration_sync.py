"""
registration_sync.py — סנכרון אוטומטי של הרשמות לאירועים (לילה יפני / מחנה קיץ).

הלוגיקה:
1. קורא מיילים חדשים ב-INBOX שמכילים הודעות הצטרפות לקבוצת WhatsApp.
2. מוצלב עם הגיליון הרלוונטי (לילה יפני / מחנה קיץ).
3. מחזיר רשימת { name, event, week, already_in_sheet } לכל כניסה חדשה.
4. מסמן אימייל כ-"seen" לאחר עיבוד.
"""

import re
import json
import imaplib
import email as email_lib
import email.header
import os
import logging
from pathlib import Path

log = logging.getLogger(__name__)

GMAIL_USER     = os.environ.get("GMAIL_USER", "topazjudo@gmail.com")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS", "")

_DATA_DIR  = Path("/data") if Path("/data").exists() else Path(".")
_SEEN_FILE = _DATA_DIR / "seen_registration_emails.json"

EVENTS = {
    "לילה יפני": "lyla",
    "מחנה":      "camp",
}

SINCE_DATE = "01-May-2026"


def _load_seen() -> set:
    if _SEEN_FILE.exists():
        return set(json.loads(_SEEN_FILE.read_text(encoding="utf-8")))
    return set()


def _save_seen(seen: set):
    _SEEN_FILE.write_text(json.dumps(list(seen), ensure_ascii=False), encoding="utf-8")


def _decode_header(raw) -> str:
    parts = email.header.decode_header(raw or "")
    out = []
    for part, enc in parts:
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(str(part))
    return " ".join(out)


def _get_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return ""


def _parse_registrations(body: str) -> list[dict]:
    """
    מחלץ שורות הצטרפות בפורמט:
    "שם משפחה שם פרטי הצטרפ.ה לקבוצה [event_name] בתאריך DD/MM/YYYY HH:MM"
    """
    results = []
    pattern = re.compile(
        r'^(.+?)\s+הצטרפ[^\s]*\s+לקבוצה\s+(.+?)\s+בתאריך\s+(\d{2}/\d{2}/\d{4})',
        re.MULTILINE
    )
    for m in pattern.finditer(body):
        raw_name   = m.group(1).strip()
        group_name = m.group(2).strip()
        date_str   = m.group(3).strip()

        # היפוך: "כהן איתן" → "איתן כהן"
        parts = raw_name.split()
        full_name = (" ".join(parts[1:]) + " " + parts[0]) if len(parts) >= 2 else raw_name

        event_type = None
        for kw, ev in EVENTS.items():
            if kw in group_name:
                event_type = ev
                break
        if not event_type:
            continue

        week = "שבועיים"
        if "שבוע ראשון" in group_name:
            week = "שבוע ראשון"
        elif "שבוע שני" in group_name:
            week = "שבוע שני"

        results.append({
            "name":       full_name,
            "raw_name":   raw_name,
            "event":      event_type,
            "group_name": group_name,
            "week":       week,
            "date":       date_str,
        })

    return results


def _name_in_list(name: str, existing: list[str]) -> bool:
    name_n = name.strip().replace("'", "").replace('"', "")
    for ex in existing:
        ex_n = ex.strip().replace("'", "").replace('"', "")
        if name_n == ex_n or name_n in ex_n or ex_n in name_n:
            return True
    return False


def fetch_new_registrations() -> list[dict]:
    """מחזיר הרשמות חדשות שלא עובדו."""
    if not GMAIL_APP_PASS:
        log.warning("GMAIL_APP_PASS not set")
        return []

    seen = _load_seen()
    results = []

    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(GMAIL_USER, GMAIL_APP_PASS)
        imap.select("INBOX")

        _, data = imap.search(None, f'(SINCE {SINCE_DATE})')
        msg_ids = data[0].split() if data[0] else []

        for mid in msg_ids:
            mid_str = mid.decode()
            if mid_str in seen:
                continue

            _, msg_data = imap.fetch(mid, "(RFC822)")
            msg = email_lib.message_from_bytes(msg_data[0][1])
            subject = _decode_header(msg.get("Subject", ""))
            body    = _get_body(msg)

            if "הצטרפ" not in body and "הצטרפ" not in subject:
                continue
            if not any(kw in (body + subject) for kw in EVENTS.keys()):
                continue

            for entry in _parse_registrations(body):
                entry["email_id"] = mid_str
                results.append(entry)

        imap.logout()

    except Exception as e:
        log.error(f"registration_sync IMAP error: {e}")

    return results


def cross_reference(registrations: list[dict]) -> list[dict]:
    """מוסיף already_in_sheet לכל הרשמה."""
    try:
        import lyla_sheet
        lyla_names = [s["name"] for s in lyla_sheet.get_students()]
    except Exception:
        lyla_names = []

    try:
        import camp_sheet
        camp_names = [s["name"] for s in camp_sheet.get_students()]
    except Exception:
        camp_names = []

    for reg in registrations:
        names = lyla_names if reg["event"] == "lyla" else camp_names
        reg["already_in_sheet"] = _name_in_list(reg["name"], names)

    return registrations


def mark_emails_seen(email_ids: list[str]):
    seen = _load_seen()
    seen.update(email_ids)
    _save_seen(seen)


def add_to_sheet(name: str, event: str, week: str, grade: str = "", branch: str = "") -> bool:
    """מוסיף תלמיד לגיליון. מחזיר True אם נוסף."""
    try:
        if event == "lyla":
            import lyla_sheet
            return lyla_sheet.add_student_direct(name, grade, branch)
        else:
            import camp_sheet
            existing = [s["name"] for s in camp_sheet.get_students()]
            if _name_in_list(name, existing):
                return False
            camp_sheet.add_student(name, grade, branch, week)
            return True
    except Exception as e:
        log.error(f"add_to_sheet error for {name}: {e}")
        return False


def run_sync_and_report() -> str:
    """
    מריץ סנכרון מלא ומחזיר טקסט לשליחה בטלגרם.
    מחזיר "" אם אין כלום חדש.
    """
    regs = fetch_new_registrations()
    if not regs:
        return ""

    regs = cross_reference(regs)

    new_regs  = [r for r in regs if not r["already_in_sheet"]]
    seen_regs = [r for r in regs if r["already_in_sheet"]]

    if not new_regs and not seen_regs:
        return ""

    lines = ["📋 *עדכון הרשמות — נמצאו כניסות חדשות*\n"]

    if new_regs:
        lines.append("✅ *נוסף לגיליון:*")
        for r in new_regs:
            event_heb = "לילה יפני" if r["event"] == "lyla" else "מחנה קיץ"
            added = add_to_sheet(r["name"], r["event"], r["week"])
            status = "נוסף ✔" if added else "כבר קיים"
            lines.append(f"  • {r['name']} — {event_heb} ({r['week']}) — {status}")

    if seen_regs:
        lines.append("\n⚪ *כבר רשום בגיליון:*")
        for r in seen_regs:
            event_heb = "לילה יפני" if r["event"] == "lyla" else "מחנה קיץ"
            lines.append(f"  • {r['name']} — {event_heb}")

    mark_emails_seen(list({r["email_id"] for r in regs}))
    return "\n".join(lines)
