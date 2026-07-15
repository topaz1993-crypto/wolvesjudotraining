"""
camp_shirts.py
סנכרון הזמנות חולצות מחנה קיץ 2026 מטופס Google → גיליון המחנה.
"""

import os, pickle, base64
from difflib import get_close_matches
import googleapiclient.discovery

FORM_SHEET_ID = "1-GsgFmIBroLdkwwZIjWbSicAyVUZH3_a7ZLKEelInjQ"
FORM_TAB      = "תגובות לטופס 1"

CAMP_SHEET_ID = "1lDULmVEYkbbASAdG2MKiozoV1gzsYQ_P-sw_CyilhyE"
CAMP_TAB      = "רשומים"
SHIRT_COL     = "D"   # עמודת מידת חולצה


def _get_service():
    b64 = os.environ.get("GOOGLE_CREDS_B64")
    if b64:
        creds = pickle.loads(base64.b64decode(b64 + "=="))
    else:
        with open(os.path.expanduser("~/token.pickle"), "rb") as f:
            creds = pickle.load(f)
    return googleapiclient.discovery.build("sheets", "v4", credentials=creds)


def _norm(name: str) -> str:
    """Normalize name for comparison."""
    return name.strip().replace("'", "").replace('"', "").replace("  ", " ")


# ─────────────────────────────────────────────────────────────────────────────
# Reading data
# ─────────────────────────────────────────────────────────────────────────────

def read_orders() -> list[dict]:
    """
    Read all shirt orders from the Google Form responses sheet.
    Returns list of {name, size, timestamp}.
    Deduplicates by name — keeps the latest order per person.
    """
    svc = _get_service()
    rows = svc.spreadsheets().values().get(
        spreadsheetId=FORM_SHEET_ID,
        range=f"'{FORM_TAB}'!A2:C500"
    ).execute().get("values", [])

    latest: dict[str, dict] = {}
    for row in rows:
        if not row or len(row) < 3:
            continue
        ts, name, size = row[0].strip(), _norm(row[1]), row[2].strip().upper()
        if name:
            latest[name] = {"name": name, "size": size, "timestamp": ts}

    return list(latest.values())


def read_camp_registrants() -> list[dict]:
    """Read all registrants from camp sheet. Returns list of {row, name, size}."""
    svc = _get_service()
    rows = svc.spreadsheets().values().get(
        spreadsheetId=CAMP_SHEET_ID,
        range=f"'{CAMP_TAB}'!A2:D200"
    ).execute().get("values", [])

    result = []
    for i, row in enumerate(rows, start=2):
        if not row or not row[0].strip():
            continue
        result.append({
            "row":  i,
            "name": _norm(row[0]),
            "size": row[3].strip() if len(row) > 3 else "",
        })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Matching
# ─────────────────────────────────────────────────────────────────────────────

def _match_name(order_name: str, camp_names: list[str]):
    """Fuzzy-match an order name to a camp registrant name."""
    # Exact match
    if order_name in camp_names:
        return order_name
    # Word-set match (handles reversed name order: "צוף חמד לוי" ↔ "חמד צוף לוי")
    order_words = set(order_name.split())
    for name in camp_names:
        if set(name.split()) == order_words:
            return name
    # Fuzzy match
    matches = get_close_matches(order_name, camp_names, n=1, cutoff=0.72)
    return matches[0] if matches else None


# ─────────────────────────────────────────────────────────────────────────────
# Sync
# ─────────────────────────────────────────────────────────────────────────────

def sync_to_camp() -> dict:
    """
    Match shirt orders to camp registrants and update camp sheet.
    Returns summary dict: {updated, unmatched_orders, missing_orders, already_set}.
    """
    orders     = read_orders()
    registrants = read_camp_registrants()
    camp_names  = [r["name"] for r in registrants]
    name_to_row = {r["name"]: r["row"] for r in registrants}

    updates        = []
    updated_names  = []
    unmatched      = []
    already_set    = []

    for order in orders:
        matched = _match_name(order["name"], camp_names)
        if not matched:
            unmatched.append(order["name"])
            continue
        row = name_to_row[matched]
        reg = next(r for r in registrants if r["name"] == matched)
        if reg["size"] and reg["size"] == order["size"]:
            already_set.append(matched)
            continue
        updates.append({
            "range": f"'{CAMP_TAB}'!{SHIRT_COL}{row}",
            "values": [[order["size"]]]
        })
        updated_names.append(f"{matched} → {order['size']}")

    if updates:
        svc = _get_service()
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=CAMP_SHEET_ID,
            body={"valueInputOption": "RAW", "data": updates}
        ).execute()

    ordered_names = {_match_name(o["name"], camp_names) or "" for o in orders}
    missing = [r["name"] for r in registrants if r["name"] not in ordered_names and not r["size"]]

    return {
        "updated":         updated_names,
        "unmatched_orders": unmatched,
        "missing_orders":  missing,
        "already_set":     already_set,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

def size_summary() -> dict:
    """
    Returns size counts from form orders + camp sheet.
    {size: count} sorted by size.
    """
    orders = read_orders()
    counts: dict[str, int] = {}
    for o in orders:
        s = o["size"]
        counts[s] = counts.get(s, 0) + 1
    return dict(sorted(counts.items()))


def missing_orders() -> list[str]:
    """Names of camp registrants who haven't ordered a shirt yet."""
    orders      = read_orders()
    registrants = read_camp_registrants()
    camp_names  = [r["name"] for r in registrants]

    ordered = set()
    for o in orders:
        m = _match_name(o["name"], camp_names)
        if m:
            ordered.add(m)
    # Also include those who already have a size in the sheet
    for r in registrants:
        if r["size"]:
            ordered.add(r["name"])

    return [r["name"] for r in registrants if r["name"] not in ordered]


# ─────────────────────────────────────────────────────────────────────────────
# Formatting for bot
# ─────────────────────────────────────────────────────────────────────────────

def format_summary() -> str:
    orders = read_orders()
    counts = size_summary()
    missing = missing_orders()
    total = len(read_camp_registrants())

    lines = [f"👕 *סיכום הזמנות חולצה — מחנה קיץ 2026*\n"]
    if counts:
        lines.append("*מידות שהוזמנו:*")
        size_order = ["XXS", "XS", "S", "M", "L", "XL", "XXL"]
        for s in size_order:
            if s in counts:
                lines.append(f"  {s}: {counts[s]}")
        for s in counts:
            if s not in size_order:
                lines.append(f"  {s}: {counts[s]}")
        lines.append(f"\n  סה\"כ הזמנות: *{len(orders)}*")
    else:
        lines.append("⚠️ אין הזמנות עדיין")

    lines.append(f"\n📋 רשומים למחנה: *{total}*")
    lines.append(f"❓ טרם הזמינו: *{len(missing)}*")
    return "\n".join(lines)


def format_sync_result(result: dict) -> str:
    lines = ["🔄 *סנכרון הזמנות חולצה*\n"]

    if result["updated"]:
        lines.append(f"✅ עודכנו {len(result['updated'])} רשומים:")
        for u in result["updated"]:
            lines.append(f"  • {u}")
    else:
        lines.append("✅ אין עדכונים חדשים")

    if result["unmatched_orders"]:
        lines.append(f"\n⚠️ לא נמצאו ברשימה ({len(result['unmatched_orders'])}):")
        for u in result["unmatched_orders"]:
            lines.append(f"  • {u}")

    if result["missing_orders"]:
        lines.append(f"\n❓ טרם הזמינו ({len(result['missing_orders'])}):")
        for m in result["missing_orders"][:15]:
            lines.append(f"  • {m}")
        if len(result["missing_orders"]) > 15:
            lines.append(f"  ... ועוד {len(result['missing_orders']) - 15}")

    return "\n".join(lines)


def format_missing() -> str:
    missing = missing_orders()
    if not missing:
        return "✅ כולם הזמינו חולצה!"
    lines = [f"❓ *טרם הזמינו חולצה ({len(missing)}/{len(read_camp_registrants())})*\n"]
    for m in missing:
        lines.append(f"  • {m}")
    return "\n".join(lines)
