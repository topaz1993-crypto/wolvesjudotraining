"""
financial_report.py — דוח כספי מלא | Wolves Judo
==================================================
פקודות:
  /report         — דוח חודשי נוכחי
  /report [חודש] — דוח לחודש ספציפי (לדוגמה: /report מרץ)
  /report שנתי   — דוח שנתי מלא ספטמבר–יולי

מקורות נתונים:
  • Grow  — גיליון דוח תשלומים (PAYMENTS_SHEET_ID)
  • סירקין — גיליון סירקין (SIRKEEN_SHEET_ID)
  • הוצאות — גיליון סיכום הכנסות (SUMMARY_SHEET_ID)

מחבר לבוט הקיים:
  1. העתק קובץ זה לתיקיית הבוט ב-Render
  2. ב-bot.py הוסף:
       from financial_report import cmd_report, cmd_report_annual
       app.add_handler(CommandHandler("report", cmd_report))
"""

from __future__ import annotations
import logging
from datetime import date, datetime
from typing import Optional

log = logging.getLogger(__name__)

# ── מזהי גיליונות (כבר קיימים ב-system_prompt) ──
PAYMENTS_SHEET_ID = "1hzkQZhmtIPL2S11Z399OmJik3pqKyOQsFp33tTNij5o"
SIRKEEN_SHEET_ID  = "1L0mcnpBPW4_3nsxaMy3EunQuOHPjWejvL1Wb6SGzltQ"

# ── נתונים קבועים מאומתים מהשיחה ──
# סירקין — לפי תאריך כניסה לחשבון (PDFs מאומתים)
SIRKEEN_BY_MONTH = {
    "ספטמבר":  0,
    "אוקטובר": 17514,
    "נובמבר":  34916,
    "דצמבר":   28350,
    "ינואר":   30590,
    "פברואר":  61523,   # 75943 + 75944
    "מרץ":     33180,
    "אפריל":   62468,   # 76118 (מרץ + מחנה פסח)
    "מאי":     0,
    "יוני":    0,
    "יולי":    0,
}

# שכר עובדים מהמיילים לרו"ח
SALARY_BY_MONTH = {
    "ספטמבר":  {"עמית": 1250, "נועה": 320,  "נדב": 240,  "בועז": 0,    "יהלי": 0},
    "אוקטובר": {"עמית": 550,  "נועה": 400,  "נדב": 120,  "בועז": 400,  "יהלי": 0},
    "נובמבר":  {"עמית": 0,    "נועה": 400,  "נדב": 800,  "בועז": 280,  "יהלי": 0},
    "דצמבר":   {"עמית": 0,    "נועה": 320,  "נדב": 1640, "בועז": 520,  "יהלי": 0},
    "ינואר":   {"עמית": 0,    "נועה": 240,  "נדב": 360,  "בועז": 480,  "יהלי": 120},
    "פברואר":  {"עמית": 0,    "נועה": 0,    "נדב": 360,  "בועז": 280,  "יהלי": 0},
    "מרץ":     {"עמית": 0,    "נועה": 0,    "נדב": 1980, "בועז": 1860, "יהלי": 0},
    "אפריל":   {"עמית": 0,    "נועה": 0,    "נדב": 0,    "בועז": 0,    "יהלי": 0},
    "מאי":     {},
    "יוני":    {},
    "יולי":    {},
}

# ביטוח לאומי (מהמייל) — ספטמבר שייך לעונה הקודמת
BITUACH_BY_MONTH = {
    "ספטמבר":  0,        # שייך לעונת 2024–2025
    "אוקטובר": 10703,
    "נובמבר":  10703,
    "דצמבר":   10703,
    "ינואר":   10703,
    "פברואר":  10703,
    "מרץ":     10703,
    "אפריל":   4794,     # פריסה הסתיימה
    "מאי":     4794,
    "יוני":    4794,
    "יולי":    4794,
}

# הוצאות קבועות לחודש
FIXED_EXPENSES = {
    "ניהול חשבונות (ישיר בע\"מ)": 660,
    "שכירות נווה ירק":            660,
    "שכירות אהרונוביץ׳":          100,
}

# עמלות Grow (חלקי — מהגיליון עד ינואר)
GROW_COMM_BY_MONTH = {
    "ספטמבר":  0,
    "אוקטובר": 11263,
    "נובמבר":  21930,
    "דצמבר":   20866,
    "ינואר":   20095,
    "פברואר":  0,    # ⏳ חסר
    "מרץ":     0,    # ⏳ חסר
    "אפריל":   0,    # ⏳ חסר
    "מאי":     0,
    "יוני":    0,
    "יולי":    0,
}

SEASON_MONTHS = [
    "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
    "ינואר", "פברואר", "מרץ", "אפריל",
    "מאי", "יוני", "יולי",
]

MONTH_TO_NUM = {
    "ספטמבר": 9, "אוקטובר": 10, "נובמבר": 11, "דצמבר": 12,
    "ינואר": 1, "פברואר": 2, "מרץ": 3, "אפריל": 4,
    "מאי": 5, "יוני": 6, "יולי": 7,
}


def _current_month_he() -> str:
    """החודש הנוכחי בעברית."""
    now = datetime.now()
    num_to_he = {v: k for k, v in MONTH_TO_NUM.items()}
    return num_to_he.get(now.month, "?")


def _get_grow_income(sheets_client, month_he: str) -> tuple[int, int]:
    """
    שולף הכנסות Grow מגיליון התשלומים.
    מחזיר: (סכום_הכנסות, מספר_תשלומים)
    
    הגיליון כבר קיים — payments_sheet.py מטפל בקריאה.
    """
    try:
        # ייבא את המודול הקיים
        import payments_sheet as ps
        data = ps.get_monthly_totals(month_he)
        if data:
            return int(data.get("total", 0)), int(data.get("count", 0))
    except Exception as e:
        log.warning("Grow income fetch error for %s: %s", month_he, e)
    return 0, 0


def _get_products_income(sheets_client, month_he: str) -> dict[str, int]:
    """
    שולף הכנסות מוצרים (חליפות, חגורות, פטצ'ים) מגיליון התשלומים.
    """
    try:
        import payments_sheet as ps
        return ps.get_products_income(month_he)
    except Exception as e:
        log.warning("Products income fetch error for %s: %s", month_he, e)
    return {"חליפות": 0, "חגורות": 0, "פטצים": 0}


def build_monthly_report(sheets_client, month_he: str) -> str:
    """בונה דוח חודשי מלא ומחזיר טקסט למסרון טלגרם."""

    lines = [f"📊 *דוח כספי — {month_he} 2025/26*\n"]

    # ══ הכנסות ══
    lines.append("💰 *הכנסות:*")

    # Grow
    grow_total, grow_count = _get_grow_income(sheets_client, month_he)
    if grow_total:
        lines.append(f"  💳 Grow (הורים): ₪{grow_total:,} ({grow_count} תשלומים)")
    else:
        lines.append(f"  💳 Grow (הורים): ⏳ טרם נשלף")

    # סירקין
    sirkeen = SIRKEEN_BY_MONTH.get(month_he, 0)
    if sirkeen:
        lines.append(f"  🏫 סירקין: ₪{sirkeen:,}")
    else:
        lines.append(f"  🏫 סירקין: —")

    # מוצרים
    products = _get_products_income(sheets_client, month_he)
    prod_total = sum(products.values())
    if prod_total:
        lines.append(f"  🥋 מוצרים: ₪{prod_total:,}")
        for name, val in products.items():
            if val:
                lines.append(f"    • {name}: ₪{val:,}")

    total_income = grow_total + sirkeen + prod_total
    lines.append(f"\n  *סה\"כ הכנסות: ₪{total_income:,}*\n")

    # ══ הוצאות ══
    lines.append("💸 *הוצאות:*")

    total_expenses = 0

    # קבועות
    for name, amount in FIXED_EXPENSES.items():
        lines.append(f"  🏠 {name}: ₪{amount:,}")
        total_expenses += amount

    # עמלות Grow
    grow_comm = GROW_COMM_BY_MONTH.get(month_he, 0)
    if grow_comm:
        lines.append(f"  📱 עמלות Grow: ₪{grow_comm:,}")
        total_expenses += grow_comm
    else:
        lines.append(f"  📱 עמלות Grow: ⏳ חסר")

    # שכר עובדים
    salary = SALARY_BY_MONTH.get(month_he, {})
    sal_total = sum(salary.values())
    if sal_total:
        lines.append(f"  👷 שכר עובדים: ₪{sal_total:,}")
        for emp, amount in salary.items():
            if amount:
                lines.append(f"    • {emp}: ₪{amount:,}")
        total_expenses += sal_total
    else:
        lines.append(f"  👷 שכר עובדים: —")

    # ביטוח לאומי
    bituach = BITUACH_BY_MONTH.get(month_he, 0)
    if bituach:
        lines.append(f"  🏛️ ביטוח לאומי: ₪{bituach:,}")
        total_expenses += bituach

    lines.append(f"\n  *סה\"כ הוצאות: ₪{total_expenses:,}*")
    if grow_comm == 0 and month_he not in ("ספטמבר",):
        lines.append("  ⚠️ _עמלות Grow חסרות — הוצאות חלקיות_")

    # ══ רווח ══
    profit = total_income - total_expenses
    emoji = "📈" if profit >= 0 else "📉"
    lines.append(f"\n{emoji} *רווח גולמי: ₪{profit:,}*")
    lines.append("_לפני מע\"מ ומס הכנסה_")

    return "\n".join(lines)


def build_annual_report(sheets_client) -> str:
    """בונה דוח שנתי מלא ספטמבר–יולי."""

    lines = ["📊 *דוח שנתי — עונת 2025/2026*\n"]
    lines.append(f"{'חודש':<10} {'הכנסות':>10} {'הוצאות':>10} {'רווח':>10}")
    lines.append("─" * 44)

    total_inc = total_exp = total_prf = 0

    for month in SEASON_MONTHS:
        grow, _ = _get_grow_income(sheets_client, month)
        sirkeen  = SIRKEEN_BY_MONTH.get(month, 0)
        products = _get_products_income(sheets_client, month)
        prod     = sum(products.values())

        income = grow + sirkeen + prod

        fixed   = sum(FIXED_EXPENSES.values())
        comm    = GROW_COMM_BY_MONTH.get(month, 0)
        sal     = sum(SALARY_BY_MONTH.get(month, {}).values())
        bituach = BITUACH_BY_MONTH.get(month, 0)
        expenses = fixed + comm + sal + bituach

        profit = income - expenses

        if income == 0 and expenses <= sum(FIXED_EXPENSES.values()):
            status = "⏳"
        else:
            status = "✅"

        lines.append(
            f"{status} {month:<9} ₪{income:>7,} ₪{expenses:>7,} ₪{profit:>7,}"
        )

        total_inc += income
        total_exp += expenses
        total_prf += profit

    lines.append("─" * 44)
    lines.append(
        f"{'סה\"כ':<10} ₪{total_inc:>7,} ₪{total_exp:>7,} ₪{total_prf:>7,}"
    )
    lines.append("\n_הוצאות חלקיות — מע\"מ, מס הכנסה ועמלות Grow חסרים לחודשים מסוימים_")

    return "\n".join(lines)


# ════════════════════════════════════════════════════
# פקודות טלגרם — מחברות לבוט הקיים
# ════════════════════════════════════════════════════

async def cmd_report(update, context):
    """
    /report          — דוח חודש נוכחי
    /report מרץ      — דוח לחודש ספציפי
    /report שנתי     — דוח שנתי מלא
    """
    await update.message.chat.send_action("typing")

    args = context.args or []
    arg  = " ".join(args).strip() if args else ""

    # ייבא את ה-sheets client הקיים מהבוט
    try:
        from payments_sheet import get_sheets_client
        gc = get_sheets_client()
    except Exception:
        gc = None  # יעבוד עם נתונים קבועים

    if arg == "שנתי":
        text = build_annual_report(gc)
    elif arg in SEASON_MONTHS:
        text = build_monthly_report(gc, arg)
    elif not arg:
        month_he = _current_month_he()
        text = build_monthly_report(gc, month_he)
    else:
        months_list = " | ".join(SEASON_MONTHS)
        await update.message.reply_text(
            f"❌ חודש לא מוכר: *{arg}*\n\n"
            f"חודשים זמינים:\n{months_list}\n\n"
            f"או שלח `/report שנתי` לדוח מלא",
            parse_mode="Markdown"
        )
        return

    # שלח בחלקים אם ארוך מדי
    if len(text) <= 4096:
        await update.message.reply_text(text, parse_mode="Markdown")
    else:
        # חלק לשניים
        mid = len(text) // 2
        split = text.rfind("\n", 0, mid)
        await update.message.reply_text(text[:split], parse_mode="Markdown")
        await update.message.reply_text(text[split:], parse_mode="Markdown")


async def cmd_report_annual(update, context):
    """קיצור /annual לדוח שנתי."""
    context.args = ["שנתי"]
    await cmd_report(update, context)


# ════════════════════════════════════════════════════
# עדכון נתוני הוצאות — פקודה לטופז
# ════════════════════════════════════════════════════

async def cmd_update_expense(update, context):
    """
    /expense מרץ grow_comm 19500
    /expense פברואר vat 8500
    עדכון נתונים חסרים — שמור בקובץ JSON מקומי
    """
    import json
    from pathlib import Path

    EXPENSE_FILE = Path("expense_overrides.json")
    args = context.args or []

    if len(args) < 3:
        await update.message.reply_text(
            "📝 *עדכון הוצאה*\n\n"
            "פורמט: `/expense [חודש] [סוג] [סכום]`\n\n"
            "סוגים:\n"
            "  `grow_comm` — עמלות Grow\n"
            "  `vat` — מע\"מ\n"
            "  `income_tax` — מס הכנסה\n"
            "  `salary_[שם]` — שכר (לדוגמה: salary_נדב)\n\n"
            "דוגמה: `/expense מרץ grow_comm 19500`",
            parse_mode="Markdown"
        )
        return

    month_he, key, *rest = args
    try:
        amount = int(rest[0])
    except ValueError:
        await update.message.reply_text("❌ סכום לא תקין")
        return

    if month_he not in SEASON_MONTHS:
        await update.message.reply_text(f"❌ חודש לא מוכר: {month_he}")
        return

    # שמור override
    overrides = json.loads(EXPENSE_FILE.read_text()) if EXPENSE_FILE.exists() else {}
    overrides.setdefault(month_he, {})[key] = amount
    EXPENSE_FILE.write_text(json.dumps(overrides, ensure_ascii=False, indent=2))

    await update.message.reply_text(
        f"✅ עודכן: *{month_he}* | {key} = ₪{amount:,}",
        parse_mode="Markdown"
    )
