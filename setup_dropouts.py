"""
One-time setup script:
1. Creates a "פורשים" sheet in each spreadsheet
2. Moves existing dropouts there
3. Cleans empty rows and renumbers active students
"""

import attendance as att
from datetime import datetime

TODAY = datetime.now().strftime("%d/%m/%Y")
DROPOUT_SHEET = "פורשים"
DROPOUT_HEADERS = ["שם", "שם משפחה", "קבוצה", "תאריך פרישה"]


def get_service():
    return att._get_service()


def sheet_exists(service, spreadsheet_id, name):
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return any(s["properties"]["title"] == name for s in meta["sheets"])


def create_dropout_sheet(service, spreadsheet_id):
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": DROPOUT_SHEET}}}]}
    ).execute()
    # Add headers
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{DROPOUT_SHEET}!A1:D1",
        valueInputOption="RAW",
        body={"values": [DROPOUT_HEADERS]}
    ).execute()
    print(f"  ✅ Created '{DROPOUT_SHEET}' sheet")


def get_active_and_dropouts(rows):
    """
    Returns (active, dropouts) where each is list of [first_name, last_name].
    Active = rows with name in col B, before or within the numbered list.
    Dropouts = rows with name in col B that appear AFTER empty-name rows.
    """
    entries = []  # (has_name, first, last)
    for row in rows[3:]:  # skip header rows
        first = row[1].strip() if len(row) > 1 else ""
        last = row[2].strip() if len(row) > 2 else ""
        entries.append((bool(first), first, last))

    # Find last index that has a name, then find first gap before it
    named_indices = [i for i, (has, _, __) in enumerate(entries) if has]
    if not named_indices:
        return [], []

    # Find the gap: first empty-name row after which there are more named rows
    gap_start = None
    for i in range(len(entries) - 1):
        has, _, __ = entries[i]
        if not has:
            # Check if there's a named row after this
            after = [j for j in named_indices if j > i]
            if after:
                gap_start = i
                break

    if gap_start is None:
        active = [[f, l] for has, f, l in entries if has]
        return active, []

    active = [[f, l] for has, f, l in entries[:gap_start] if has]
    dropouts = [[f, l] for has, f, l in entries[gap_start:] if has]
    return active, dropouts


def append_dropouts_to_sheet(service, spreadsheet_id, group, dropouts):
    if not dropouts:
        return
    # Find next empty row in dropout sheet
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{DROPOUT_SHEET}!A:A"
    ).execute()
    next_row = len(result.get("values", [])) + 1
    rows_to_add = [[f, l, group, TODAY] for f, l in dropouts]
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{DROPOUT_SHEET}!A{next_row}:D{next_row + len(rows_to_add) - 1}",
        valueInputOption="RAW",
        body={"values": rows_to_add}
    ).execute()
    print(f"  📋 Moved {len(dropouts)} dropouts to '{DROPOUT_SHEET}'")


def rewrite_active_students(service, spreadsheet_id, group, active):
    """Clear student area and rewrite only active students, numbered."""
    # Clear from row 4 downward
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{group}!A4:C200"
    ).execute()
    if not active:
        return
    rows = [[str(i + 1), f, l] for i, (f, l) in enumerate(active)]
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{group}!A4:C{3 + len(rows)}",
        valueInputOption="RAW",
        body={"values": rows}
    ).execute()
    print(f"  ✏️  Rewrote {len(active)} active students in '{group}'")


def process_branch(service, branch, spreadsheet_id, groups):
    print(f"\n=== {branch} ===")

    if not sheet_exists(service, spreadsheet_id, DROPOUT_SHEET):
        create_dropout_sheet(service, spreadsheet_id)
    else:
        print(f"  '{DROPOUT_SHEET}' already exists")

    for group in groups:
        print(f"  → {group}")
        try:
            result = service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=f"{group}!A1:C200"
            ).execute()
            rows = result.get("values", [])
            active, dropouts = get_active_and_dropouts(rows)

            if dropouts:
                print(f"    Found dropouts: {[f+' '+l for f,l in dropouts]}")
                append_dropouts_to_sheet(service, spreadsheet_id, group, dropouts)
                rewrite_active_students(service, spreadsheet_id, group, active)
            else:
                # Still clean up empty rows
                if any(not f for f, l in (([r[1].strip() if len(r)>1 else "", r[2].strip() if len(r)>2 else ""] for r in rows[3:]))):
                    rewrite_active_students(service, spreadsheet_id, group, active)
                else:
                    print(f"    No changes needed")
        except Exception as e:
            print(f"    ⚠️  Error: {e}")


def main():
    service = get_service()

    branches = {
        "סירקין": (att.BRANCH_SHEETS["סירקין"], att.BRANCH_GROUPS["סירקין"]),
        "נווה ירק": (att.BRANCH_SHEETS["נווה ירק"], att.BRANCH_GROUPS["נווה ירק"]),
        "פונקציונלי": (att.BRANCH_SHEETS["פונקציונלי"], att.BRANCH_GROUPS["פונקציונלי"]),
        "אהרונוביץ": (att.BRANCH_SHEETS["אהרונוביץ"], att.BRANCH_GROUPS["אהרונוביץ"]),
        "חגור": (att.BRANCH_SHEETS["חגור"], att.BRANCH_GROUPS["חגור"]),
    }

    for branch, (sid, groups) in branches.items():
        process_branch(service, branch, sid, groups)

    print("\n✅ Done!")


if __name__ == "__main__":
    main()
