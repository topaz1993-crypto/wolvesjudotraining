"""
Gmail reader — fetches payment-related emails since 1/9/2025.
Returns structured list of unprocessed emails.
"""

import imaplib
import email as email_lib
import email.header
import json
import os
from datetime import datetime
from pathlib import Path

GMAIL_USER    = os.environ.get("GMAIL_USER", "topazjudo@gmail.com")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS", "")

_DATA_DIR = Path("/data") if Path("/data").exists() else Path(".")
SEEN_FILE  = _DATA_DIR / "seen_emails.json"

START_DATE = "01-Sep-2025"  # IMAP date format


def _load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    return set()


def _save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen), ensure_ascii=False), encoding="utf-8")


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
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body += payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return body[:3000]


def fetch_new_emails() -> list[dict]:
    """
    Connect to Gmail, fetch emails since START_DATE that haven't been seen.
    Returns list of {id, subject, sender, date, body}.
    """
    if not GMAIL_APP_PASS:
        return []

    seen = _load_seen()
    results = []

    try:
        imap = _imap_connect()
        imap.select("INBOX")

        _, data = imap.search(None, f'(SINCE {START_DATE})')
        msg_ids = data[0].split() if data[0] else []

        for mid in msg_ids:
            mid_str = mid.decode()
            if mid_str in seen:
                continue

            _, msg_data = imap.fetch(mid, "(RFC822)")
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)

            subject = _decode_header(msg.get("Subject", ""))
            sender  = _decode_header(msg.get("From", ""))
            date    = msg.get("Date", "")
            body    = _get_body(msg)

            results.append({
                "id":      mid_str,
                "subject": subject,
                "sender":  sender,
                "date":    date,
                "body":    body,
            })

        imap.logout()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"IMAP error: {e}")

    return results


def mark_seen(email_id: str):
    seen = _load_seen()
    seen.add(email_id)
    _save_seen(seen)


def mark_skipped(email_id: str):
    """Mark email as processed (skipped — not payment related)."""
    mark_seen(email_id)


def _imap_connect(timeout: int = 20):
    """Connect to Gmail IMAP and return imap object. Raises on timeout/auth error."""
    imap = imaplib.IMAP4_SSL("imap.gmail.com", timeout=timeout)
    imap.login(GMAIL_USER, GMAIL_APP_PASS)
    return imap


def _fetch_invoice4u_emails(imap, mailbox: str = "[Gmail]/All Mail") -> list[tuple[str, str, str]]:
    """
    Fetch all emails from invoice4u in mailbox.
    Returns list of (msg_id, subject, body).
    Searches only by FROM to avoid unreliable Hebrew IMAP SEARCH.
    """
    import logging
    log = logging.getLogger(__name__)
    try:
        imap.select(mailbox, readonly=True)
    except Exception:
        try:
            imap.select("INBOX", readonly=True)
            log.warning("Fell back to INBOX (could not open %s)", mailbox)
        except Exception as e:
            log.error("Could not select mailbox: %s", e)
            return []

    _, data = imap.search(None, 'FROM "notifications@invoice4u.co.il"')
    msg_ids = data[0].split() if data[0] else []
    log.info("invoice4u emails found in %s: %d", mailbox, len(msg_ids))

    results = []
    for mid in msg_ids:
        try:
            _, msg_data = imap.fetch(mid, "(RFC822)")
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)
            subject = _decode_header(msg.get("Subject", ""))
            body = _get_body(msg)
            results.append((mid.decode(), subject, body, msg.get("Date", "")))
        except Exception as e:
            log.warning("Failed to fetch msg %s: %s", mid, e)
            continue
    return results


def search_event_registrations(event_keyword: str) -> list[dict]:
    """
    חיפוש הרשמות לאירוע לפי מילת מפתח (למשל "לילה יפני" או "מחנה").
    מחזיר רשימת { name, phone, email, price, date, event_name }.
    מחפש ב-[Gmail]/All Mail (כולל Promotions/Updates), מסנן בPython.
    """
    import re
    import logging
    log = logging.getLogger(__name__)

    if not GMAIL_APP_PASS:
        log.warning("search_event_registrations: GMAIL_APP_PASS not set")
        return []

    results = []
    seen_names = set()
    keyword_lower = event_keyword.strip().lower()

    try:
        imap = _imap_connect()
        emails = _fetch_invoice4u_emails(imap)

        for mid, subject, body, date_str in emails:
            # Filter by keyword in subject OR body
            if keyword_lower not in body.lower() and keyword_lower not in subject.lower():
                continue

            try:
                # שם האירוע
                event_match = re.search(r'שם עמוד המכירה\s*[:\-]\s*(.+?)[\n\r]', body)
                if not event_match:
                    event_match = re.search(r'שם המוצר\s*[:\-]\s*(.+?)[\n\r]', body)
                event_name = event_match.group(1).strip() if event_match else event_keyword

                # שם הלקוח
                name_match = re.search(r'שם הלקוח\s*[:\-]\s*(.+?)[\n\r]', body)
                if not name_match:
                    name_match = re.search(r'שם\s*[:\-]\s*(.+?)[\n\r]', body)
                name = name_match.group(1).strip() if name_match else ""

                # טלפון
                phone_match = re.search(r'טלפון(?:\s+הלקוח)?\s*[:\-]\s*([\d\-\+]+)', body)
                phone = phone_match.group(1).strip() if phone_match else ""

                # מחיר
                price_match = re.search(r'(?:מחיר המוצר|סכום|מחיר)\s*[:\-]\s*([\d,\.]+)', body)
                price = price_match.group(1).strip() if price_match else ""

                # מייל
                email_match = re.search(r'מייל(?:\s+הלקוח)?\s*[:\-]\s*(\S+@\S+)', body)
                customer_email = email_match.group(1).strip() if email_match else ""

                if name and name not in seen_names:
                    seen_names.add(name)
                    results.append({
                        "name": name,
                        "phone": phone,
                        "email": customer_email,
                        "price": price,
                        "date": date_str[:16],
                        "event_name": event_name,
                    })
            except Exception as e:
                log.warning("Failed to parse invoice4u email %s: %s", mid, e)
                continue

        imap.logout()

    except Exception as e:
        log.error("search_event_registrations error: %s", e)

    log.info("search_event_registrations('%s'): found %d registrations", event_keyword, len(results))
    return sorted(results, key=lambda x: x["date"])


def debug_invoice4u_emails() -> dict:
    """
    Diagnostic: connect to Gmail, count invoice4u emails, return raw body of first one.
    Returns dict with keys: connected, mailbox, total_found, first_subject, first_body, error.
    """
    import logging
    log = logging.getLogger(__name__)

    if not GMAIL_APP_PASS:
        return {"connected": False, "error": "GMAIL_APP_PASS לא מוגדר"}

    try:
        imap = _imap_connect()
        emails = _fetch_invoice4u_emails(imap)
        first = emails[0] if emails else None
        imap.logout()
        return {
            "connected": True,
            "total_found": len(emails),
            "first_subject": first[1] if first else None,
            "first_body": first[2][:800] if first else None,
            "first_date": first[3] if first else None,
            "error": None,
        }
    except Exception as e:
        log.error("debug_invoice4u_emails: %s", e)
        return {"connected": False, "error": str(e)}
