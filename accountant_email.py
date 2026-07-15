"""
accountant_email.py
יצירת דוח חודשי ושליחה לרואה חשבון (ilan@gbcpa.co.il) דרך Gmail SMTP.

דרישה חד-פעמית: GMAIL_APP_PASSWORD ב-Render environment variables.
"""

import os, smtplib, pickle, base64
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import googleapiclient.discovery
import zoneinfo

PAYMENTS_ID   = "1hzkQZhmtIPL2S11Z399OmJik3pqKyOQsFp33tTNij5o"
SUMMARY_TAB   = "סיכום הכנסות"

ACCOUNTANT_TO = "ilan@gbcpa.co.il"
ACCOUNTANT_CC = "Larissa@gbcpa.co.il"
SENDER_EMAIL  = "topaz1993@gmail.com"

IL_TZ  = zoneinfo.ZoneInfo("Asia/Jerusalem")
MONTHS = ["ספטמבר","אוקטובר","נובמבר","דצמבר","ינואר","פברואר","מרץ","אפריל","מאי","יוני","יולי"]

_CAL_TO_HEB = {
    9:"ספטמבר", 10:"אוקטובר", 11:"נובמבר", 12:"דצמבר",
    1:"ינואר",  2:"פברואר",  3:"מרץ",     4:"אפריל",
    5:"מאי",    6:"יוני",    7:"יולי",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_sheets():
    b64 = os.environ.get("GOOGLE_CREDS_B64")
    if b64:
        creds = pickle.loads(base64.b64decode(b64 + "=="))
    else:
        with open(os.path.expanduser("~/token.pickle"), "rb") as f:
            creds = pickle.load(f)
    return googleapiclient.discovery.build("sheets", "v4", credentials=creds)


def _int(val) -> int:
    try:
        return int(str(val).replace(",", "").replace("₪", "").strip())
    except Exception:
        return 0


def prev_month_name() -> str:
    """Return the previous month name in Hebrew (judo season months only)."""
    now = datetime.now(IL_TZ)
    m = now.month - 1
    if m == 0:
        m = 12
    return _CAL_TO_HEB.get(m, "יוני")


# ─────────────────────────────────────────────────────────────────────────────
# Data reading
# ─────────────────────────────────────────────────────────────────────────────

def read_month_data(month_name: str) -> dict:
    """
    Read income + salary + tax data for a month from the payments sheet.
    Returns dict with keys: month, income, salary, expenses, taxes.
    """
    svc = _get_sheets()
    rows = svc.spreadsheets().values().get(
        spreadsheetId=PAYMENTS_ID,
        range=f"'{SUMMARY_TAB}'!A1:N30"
    ).execute().get("values", [])

    if not rows:
        return {"month": month_name}

    # Find month column from first header row
    header = rows[0]
    col = None
    for i, h in enumerate(header):
        if h.strip() == month_name:
            col = i
            break
    if col is None:
        return {"month": month_name, "error": f"לא נמצא חודש '{month_name}' בגיליון"}

    def val(label: str, start: int = 0) -> int:
        for i, row in enumerate(rows):
            if i < start:
                continue
            if row and row[0].strip().rstrip("‏ ") == label.strip():
                return _int(row[col]) if len(row) > col else 0
        return 0

    # Income section (rows 1-7)
    bi   = val('עמותת ספורט סירקין')
    grow = val('משולם פתרונות סליקה')
    tot  = val('סה"כ לתשלום')
    vat  = val('מע"מ')
    it   = val('מס הכנסה')
    bl   = val('ביטוח לאומי')

    # Find where expenses section starts (second "חודש" header row)
    exp_start = 0
    for i, row in enumerate(rows):
        if i > 0 and row and row[0].strip() == "חודש":
            exp_start = i + 1
            break

    # Expenses + salary (rows after exp_start)
    rent_nv   = val('שכירות נווה ירק', exp_start)
    sal_nadav = val('משכורת נדב',       exp_start)
    sal_boaz  = val('משכורת בועז',      exp_start)
    sal_noa   = val('משכורת נועה',      exp_start)
    sal_vardy = val('משכורת ורדי',      exp_start)

    sal_items = {k: v for k, v in {
        "נדב":  sal_nadav,
        "בועז": sal_boaz,
        "נועה": sal_noa,
        "ורדי": sal_vardy,
    }.items() if v > 0}

    return {
        "month":    month_name,
        "income":   {"bi": bi, "grow": grow, "total": tot or bi + grow},
        "salary":   {"items": sal_items, "total": sum(sal_items.values())},
        "expenses": {"rent_nv": rent_nv},
        "taxes":    {"vat": vat, "income_tax": it, "bituah_leumi": bl},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Email composition
# ─────────────────────────────────────────────────────────────────────────────

def compose_email(data: dict) -> tuple[str, str]:
    """Return (subject, plain-text body) for accountant email."""
    month = data.get("month", "")
    inc   = data.get("income",   {})
    sal   = data.get("salary",   {})
    exp   = data.get("expenses", {})
    taxes = data.get("taxes",    {})

    bi   = inc.get("bi",    0)
    grow = inc.get("grow",  0)
    tot  = inc.get("total", 0) or bi + grow

    subject = f"דוח חודשי {month} — מועדון ג'ודו וולבס"

    lines = [
        "שלום אילן ולריסה,",
        "",
        f"מצורף דוח חודשי לחודש {month}:",
        "",
        "━━━━━━━━━━ הכנסות ━━━━━━━━━━",
    ]

    if bi:
        lines.append(f"• BI סירקין (פונקציונלי + מועדון + נבחרת + איפון פייט):  {bi:,} ₪")
    if grow:
        lines.append(f"• GROW סליקת אשראי וולבס (חגור + נווה ירק + אהרונוביץ + חליפות):  {grow:,} ₪")
    if tot:
        lines.append(f"• סה\"כ הכנסות:  {tot:,} ₪")

    if sal.get("items"):
        lines += ["", "━━━━━━━━━━ שכר מדריכים ━━━━━━━━━━"]
        for name, amount in sal["items"].items():
            lines.append(f"• {name}:  {amount:,} ₪")
        lines.append(f"• סה\"כ שכר:  {sal['total']:,} ₪")

    if exp.get("rent_nv"):
        lines += [
            "", "━━━━━━━━━━ הוצאות ━━━━━━━━━━",
            f"• שכירות נווה ירק:  {exp['rent_nv']:,} ₪",
        ]

    lines += [
        "", "━━━━━━━━━━ קבלות ━━━━━━━━━━",
        "הקבלות זמינות במערכת Invoice4u.",
        "ניתן לייצא CSV לפי בקשה.",
    ]

    tax_lines = []
    if taxes.get("vat"):
        tax_lines.append(f"• מע\"מ ששולם:  {taxes['vat']:,} ₪")
    if taxes.get("income_tax"):
        tax_lines.append(f"• מס הכנסה:  {taxes['income_tax']:,} ₪")
    if taxes.get("bituah_leumi"):
        tax_lines.append(f"• ביטוח לאומי:  {taxes['bituah_leumi']:,} ₪")
    if tax_lines:
        lines += ["", "━━━━━━━━━━ מיסים ששולמו ━━━━━━━━━━"] + tax_lines

    lines += [
        "",
        "אנא שלחו הוראות תשלום לחודש הבא לפי הצורך.",
        "",
        "בברכה,",
        "טופז צברי",
        "מועדון ג'ודו וולבס",
        "054-6814281",
    ]

    return subject, "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Sending
# ─────────────────────────────────────────────────────────────────────────────

def send_email(subject: str, body: str) -> str:
    """
    Send email via Gmail SMTP using App Password.
    Raises ValueError if GMAIL_APP_PASSWORD not set.
    Returns success message string.
    """
    pwd = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not pwd:
        raise ValueError(
            "GMAIL_APP_PASSWORD לא מוגדר.\n"
            "הוסף ב-Render → Environment Variables.\n"
            "יצירה: Google Account → Security → 2-Step → App Passwords → Mail."
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"]  = subject
    msg["From"]     = f"טופז צברי — וולבס ג'ודו <{SENDER_EMAIL}>"
    msg["To"]       = ACCOUNTANT_TO
    msg["Cc"]       = ACCOUNTANT_CC
    msg["Reply-To"] = SENDER_EMAIL

    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SENDER_EMAIL, pwd)
        smtp.sendmail(SENDER_EMAIL, [ACCOUNTANT_TO, ACCOUNTANT_CC], msg.as_string())

    return f"✅ מייל נשלח ל-{ACCOUNTANT_TO} (+ {ACCOUNTANT_CC})"


# ─────────────────────────────────────────────────────────────────────────────
# Bot interface
# ─────────────────────────────────────────────────────────────────────────────

def prepare_preview(month_name: str) -> tuple[dict, str]:
    """
    Called from bot.py before showing confirm/cancel buttons.
    Returns (session_data_dict, telegram_preview_text).
    """
    data             = read_month_data(month_name)
    subject, body    = compose_email(data)

    inc = data.get("income", {})
    sal = data.get("salary", {})

    preview_lines = [
        f"📧 *דוח חודשי — {month_name}*",
        "",
        f"*אל:* {ACCOUNTANT_TO}",
        f"*עותק:* {ACCOUNTANT_CC}",
        f"*מאת:* {SENDER_EMAIL}",
        f"*נושא:* {subject}",
        "",
        f"💰 BI סירקין: {inc.get('bi', 0):,} ₪",
        f"💳 GROW: {inc.get('grow', 0):,} ₪",
        f"📊 סה\"כ הכנסות: {inc.get('total', 0):,} ₪",
    ]

    sal_total = sal.get("total", 0)
    if sal_total:
        preview_lines.append(f"👥 שכר מדריכים: {sal_total:,} ₪")

    if data.get("error"):
        preview_lines.append(f"\n⚠️ {data['error']}")

    preview_lines += ["", "לשלוח מייל לרואה החשבון?"]

    sess = {"month": month_name, "subject": subject, "body": body}
    return sess, "\n".join(preview_lines)
