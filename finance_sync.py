"""
finance_sync.py
סנכרון קבצים כספיים → גיליון P&L.

תומך ב:
  - CSV מ-Invoice4u  → הכנסות לפי סניף
  - דוח שכר (Excel/PDF) → שכר מדריכים
  - תדפיס בנק (CSV) → הוצאות
  - דוח מיסים (PDF/טקסט) → מקדמות מס, מע"מ

קורא ישירות את הגיליון הנכון לפי שנת העונה שבקובץ.
"""

import os, io, csv, re, pickle, json
import openpyxl
import anthropic

PL_2025_2026 = "1BkjqlgyWVgs0n1tU-oGr7i0HQrDf_al7TRXkUlO12fM"
PL_2026_2027 = "1mZZPImSslzagtaQNm9krrqgYM5yL9rGlLojLT-5izXE"
PL_TAB       = "P&L חודשי"

MONTHS = ["ספטמבר","אוקטובר","נובמבר","דצמבר","ינואר","פברואר","מרץ","אפריל","מאי","יוני","יולי"]

# שנה לפי חודש בעונה
MONTH_YEAR_2025 = {"ספטמבר":2025,"אוקטובר":2025,"נובמבר":2025,"דצמבר":2025,
                   "ינואר":2026,"פברואר":2026,"מרץ":2026,"אפריל":2026,
                   "מאי":2026,"יוני":2026,"יולי":2026}
MONTH_YEAR_2026 = {"ספטמבר":2026,"אוקטובר":2026,"נובמבר":2026,"דצמבר":2026,
                   "ינואר":2027,"פברואר":2027,"מרץ":2027,"אפריל":2027,
                   "מאי":2027,"יוני":2027,"יולי":2027}

# שורות בגיליון (זהה בשני הגיליונות)
ROW = {
    "sirkeen":     3,
    "grouo":       4,
    "ippon":       5,
    "other":       6,
    "eidan":      17,
    "instructors":18,
    "it":         31,
    "vat":        32,
}

BRANCH_KEYWORDS = {
    "sirkeen": ["סירקין", "sirkeen"],
    "grouo":   ["גרואו", "grouo", "חגור", "נווה", "אהרונוביץ"],
    "ippon":   ["איפון", "ippon", "fight"],
}


# ─────────────────────────────────────────────────────────────────────
# Google Sheets helpers
# ─────────────────────────────────────────────────────────────────────

def _get_sheets():
    import googleapiclient.discovery
    with open(os.path.expanduser("~/.wolves_judo_token.pickle"), "rb") as f:
        creds = pickle.load(f)
    return googleapiclient.discovery.build("sheets", "v4", credentials=creds)


def _month_col(month_name: str) -> str:
    """ספטמבר → C, אוקטובר → D, ..."""
    idx = MONTHS.index(month_name)
    return chr(67 + idx)


def update_cell(sheet_id: str, row: int, month_name: str, value):
    """Write a single numeric value to the cell for (row, month)."""
    svc = _get_sheets()
    col = _month_col(month_name)
    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{PL_TAB}'!{col}{row}",
        valueInputOption="USER_ENTERED",
        body={"values": [[value]]}
    ).execute()


def apply_update(sheet_id: str, update: dict):
    """
    Apply extracted data dict to a P&L sheet.
    update = {
      "month": "ינואר",
      "income": {"sirkeen": 28000, "grouo": 9000, "ippon": 2500},
      "salary": {"eidan": 927, "instructors": 1200},
      "tax": {"it": 9000, "vat": 15000},
    }
    Returns summary string.
    """
    svc = _get_sheets()
    month = update["month"]
    col   = _month_col(month)
    lines = []

    def write(row_key, value):
        r = ROW[row_key]
        svc.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{PL_TAB}'!{col}{r}",
            valueInputOption="USER_ENTERED",
            body={"values": [[value]]}
        ).execute()

    income = update.get("income", {})
    for k in ["sirkeen", "grouo", "ippon", "other"]:
        if k in income:
            write(k, income[k])
            labels = {"sirkeen": "סירקין", "grouo": "גרואו", "ippon": "איפון פייט", "other": "אחרות"}
            lines.append(f"  הכנסות {labels[k]}: {income[k]:,} ₪")

    salary = update.get("salary", {})
    for k in ["eidan", "instructors"]:
        if k in salary:
            write(k, salary[k])
            labels = {"eidan": "עידן ורדי", "instructors": "מדריכים"}
            lines.append(f"  שכר {labels[k]}: {salary[k]:,} ₪")

    tax = update.get("tax", {})
    for k in ["it", "vat"]:
        if k in tax:
            write(k, tax[k])
            labels = {"it": "מס הכנסה", "vat": "מע\"מ"}
            lines.append(f"  {labels[k]}: {tax[k]:,} ₪")

    return f"✅ עודכן {month}:\n" + "\n".join(lines)


def which_sheet(year_in_file: int) -> str:
    """Return the right P&L sheet ID based on the year found in the file."""
    if year_in_file and year_in_file <= 2026:
        return PL_2025_2026
    return PL_2026_2027


# ─────────────────────────────────────────────────────────────────────
# Text extraction from various file formats
# ─────────────────────────────────────────────────────────────────────

def extract_text(filename: str, data: bytes) -> str:
    """Extract text content from CSV, XLSX, XLS, or PDF."""
    fname = filename.lower()

    if fname.endswith(".csv"):
        for enc in ("utf-8-sig", "windows-1255", "utf-8", "iso-8859-8"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("latin-1", errors="replace")

    if fname.endswith(".xlsx"):
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
        lines = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None and str(c).strip() not in ("", "None")]
                if cells:
                    lines.append(" | ".join(cells))
        return "\n".join(lines[:500])  # cap at 500 rows

    if fname.endswith(".xls"):
        import xlrd
        wb = xlrd.open_workbook(file_contents=data)
        lines = []
        for s in wb.sheets():
            for r in range(s.nrows):
                cells = [str(s.cell_value(r, c)) for c in range(s.ncols)
                         if str(s.cell_value(r, c)).strip() not in ("", "0.0")]
                if cells:
                    lines.append(" | ".join(cells))
        return "\n".join(lines[:500])

    if fname.endswith(".pdf"):
        from pdfminer.high_level import extract_text as pdf_extract
        return pdf_extract(io.BytesIO(data))[:8000]  # cap at 8K chars

    # Fallback: try to decode as text
    try:
        return data.decode("utf-8-sig")
    except Exception:
        return data.decode("latin-1", errors="replace")


# ─────────────────────────────────────────────────────────────────────
# Claude-based extraction
# ─────────────────────────────────────────────────────────────────────

EXTRACT_PROMPT = """
אתה מנתח קבצים כספיים של מועדון ג'ודו. קרא את הטקסט הבא מקובץ כספי וחלץ נתונים.

החזר JSON בפורמט הזה (השמט שדות שאין לך נתונים עליהם):
{
  "file_type": "invoice4u_csv | salary_report | bank_statement | tax_report",
  "month": "שם חודש בעברית (ספטמבר/אוקטובר/.../יולי)",
  "year": 2026,
  "income": {
    "sirkeen": 28000,
    "grouo": 9000,
    "ippon": 2500
  },
  "salary": {
    "eidan": 927,
    "instructors": 1200
  },
  "tax": {
    "it": 9000,
    "vat": 15000
  }
}

מידע על המועדון:
- סניפים: סירקין (הכי גדול), גרואו (חגור + נווה ירק + אהרונוביץ), איפון פייט
- מדריכים: עידן ורדי (flat 927/חודש), נדב, בועז, נועה הדר, יהלי (דרך דיקלה ניצן)
- "instructors" = סך כל שכר מדריכים פרט לעידן ורדי
- מקדמות מס הכנסה = מס שוטף, לא חוב שומות
- מע"מ = רק כשיש תשלום דו-חודשי

אם הקובץ הוא Invoice4u — חשב סכום תשלומים לפי סניף.
אם הקובץ הוא תדפיס בנק — חשב רק הוצאות עסקיות.

החזר JSON בלבד, ללא טקסט נוסף.

---
תוכן הקובץ:
"""


def extract_with_claude(text: str) -> dict:
    """Send file text to Claude and return parsed financial data."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set")

    _client = anthropic.Anthropic(api_key=api_key)
    msg = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": EXTRACT_PROMPT + text[:6000]}]
    )
    raw = msg.content[0].text.strip()

    # Strip markdown fences if present
    raw = re.sub(r"^```json?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    return json.loads(raw)


# ─────────────────────────────────────────────────────────────────────
# Main entry point: process uploaded file
# ─────────────────────────────────────────────────────────────────────

def process_file(filename: str, data: bytes) -> tuple[dict, str]:
    """
    Main function called from bot.py.
    Returns (extracted_data, preview_text).
    Raises on error.
    """
    text = extract_text(filename, data)
    extracted = extract_with_claude(text)

    month = extracted.get("month", "")
    year  = extracted.get("year", 2026)
    sheet_id = which_sheet(year)
    sheet_label = "2025-2026" if sheet_id == PL_2025_2026 else "2026-2027"

    lines = [
        f"📄 *קובץ:* {filename}",
        f"📅 *חודש:* {month} {year}  |  📊 *גיליון:* {sheet_label}",
        "",
    ]

    income = extracted.get("income", {})
    if income:
        lines.append("💰 *הכנסות:*")
        labels = {"sirkeen": "סירקין", "grouo": "גרואו", "ippon": "איפון פייט"}
        for k, lbl in labels.items():
            if k in income:
                lines.append(f"  {lbl}: {income[k]:,} ₪")

    salary = extracted.get("salary", {})
    if salary:
        lines.append("👥 *שכר מדריכים:*")
        if "eidan" in salary:
            lines.append(f"  עידן ורדי: {salary['eidan']:,} ₪")
        if "instructors" in salary:
            lines.append(f"  מדריכים אחרים: {salary['instructors']:,} ₪")

    tax = extracted.get("tax", {})
    if tax:
        lines.append("📋 *מיסים:*")
        if "it" in tax:
            lines.append(f"  מס הכנסה: {tax['it']:,} ₪")
        if "vat" in tax:
            lines.append(f"  מע\"מ: {tax['vat']:,} ₪")

    if not income and not salary and not tax:
        lines.append("⚠️ לא זוהו נתונים כספיים מוכרים בקובץ.")

    extracted["_sheet_id"] = sheet_id
    preview = "\n".join(lines)
    return extracted, preview
