"""
Wolves Judo — Training Plan Agent + Attendance (Telegram Bot)
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path

import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
import attendance as att
import absence_tracker as abt
import calendar_tasks as cal
import camp_sheet as camp
import lyla_sheet as lyla
import competitions_sheet as comp_sheet
import training_plans as tp
import email_reader
import payments_sheet
import payments_report
import dropout_detector
import weekly_schedule as ws
import training_archive as arc
import contacts as contacts_db
import invoice4u_reader
import invoice4u_sync
import payment_matcher
import registration_sync
import conversation_log
import wa_client

# Israel timezone
import zoneinfo as _zoneinfo
IL_TZ = _zoneinfo.ZoneInfo("Asia/Jerusalem")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
GMAIL_USER        = os.environ.get("GMAIL_USER", "topazjudo@gmail.com")
GMAIL_APP_PASS    = os.environ.get("GMAIL_APP_PASS", "")
TOPAZ_CHAT_ID     = os.environ.get("TOPAZ_CHAT_ID", "")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = open("system_prompt.txt", encoding="utf-8").read()

# Use /data if available (Render persistent disk — survives deploys), else local
_DATA_DIR = Path("/data") if Path("/data").exists() else Path(".")
HISTORY_FILE     = _DATA_DIR / "conversation_history.json"
LOG_FILE         = _DATA_DIR / "training_log.json"
PENDING_FILE     = _DATA_DIR / "pending_plans.json"
CORRECTIONS_FILE = _DATA_DIR / "corrections.txt"
WA_FAVORITES_FILE = _DATA_DIR / "wa_favorite_groups.json"

WOLVES_KEYWORDS = ["wolves", "wolf", "ג'ודו", 'ג׳ודו', "וולבס", "טופז", "judo", "גודו", "איפון פייט", "מועדון הג"]

def _load_wa_favs() -> dict:
    try:
        with open(WA_FAVORITES_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def _save_wa_favs(favs: dict) -> None:
    with open(WA_FAVORITES_FILE, "w") as f:
        json.dump(favs, f, ensure_ascii=False)


def load_corrections() -> str:
    if CORRECTIONS_FILE.exists():
        return CORRECTIONS_FILE.read_text(encoding="utf-8").strip()
    return ""


def append_correction(correction: str):
    from datetime import date
    line = f"[{date.today().isoformat()}] {correction}\n"
    with open(CORRECTIONS_FILE, "a", encoding="utf-8") as f:
        f.write(line)


def build_system_prompt() -> str:
    corrections = load_corrections()
    if not corrections:
        return SYSTEM_PROMPT
    return SYSTEM_PROMPT + "\n\n---\n## תיקונים ולמידה מהשטח (עודכן אוטומטית)\n" + corrections


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


history: dict[str, list] = load_json(HISTORY_FILE, {})
training_log: dict = load_json(LOG_FILE, {})
# pending_plans[user_id] = last proposed plan text (before approval)
pending_plans: dict[str, str] = load_json(PENDING_FILE, {})
# attendance_sessions[user_id] = active attendance session dict
attendance_sessions: dict[str, dict] = {}
# pending_payments[key] = {student, month, amount, email_id, subject, sender}
pending_payments: dict[str, dict] = {}
# action_history[user_id] = list of {type, description, undo_fn_name, undo_data}
action_history: dict[str, list] = {}
# sheets_sessions[user_id] = active camp/lyla flow session
sheets_sessions: dict[str, dict] = {}
# pending_belt_events[user_id] = {child_name, belt_color, ceremony_day}
pending_belt_events: dict[str, dict] = {}
# new_student_sessions[user_id] = {"session": ..., "step": "first_name"|"last_name", "first_name": "..."}
new_student_sessions: dict[str, dict] = {}
# calendar_sessions[user_id] = {"step": "pick_cal"|"pick_date"|"pick_title", "calendar": ..., "date": ..., "title": ...}
calendar_sessions: dict[str, dict] = {}
# payment_sync_sessions[user_id] = active invoice4u sync flow state
payment_sync_sessions: dict[str, dict] = {}


def get_history(user_id: str) -> list:
    return history.setdefault(user_id, [])


def append_history(user_id: str, role: str, content: str):
    get_history(user_id).append({"role": role, "content": content})
    if len(history[user_id]) > 40:
        history[user_id] = history[user_id][-40:]
    save_json(HISTORY_FILE, history)


def get_recent_trainings(branch: str, group: str, n: int = 5) -> str:
    key = f"{branch}_{group}"
    entries = training_log.get(key, [])[-n:]
    if not entries:
        return ""
    lines = [f"  • {e['date']}: {e['topic']}" for e in entries]
    return "אימונים אחרונים ב" + branch + " — " + group + ":\n" + "\n".join(lines)


def attendance_done_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("➕ יש מצטרף חדש", callback_data="new_student"),
        InlineKeyboardButton("✅ סיום", callback_data="att_done"),
    ]])


def new_student_again_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("➕ יש עוד מצטרף", callback_data="new_student"),
        InlineKeyboardButton("✅ סיום", callback_data="att_done"),
    ]])


def recent_events_buttons(events: list) -> InlineKeyboardMarkup:
    rows = []
    for i, e in enumerate(events):
        emoji = cal.CALENDAR_EMOJI.get(e["calendar_name"], "📅")
        time_str = f" {e['time']}" if e.get("time") else ""
        label = f"🗑 {emoji} {e['title']} | {e['date']}{time_str}"
        rows.append([InlineKeyboardButton(label, callback_data=f"cal_del_{i}")])
    rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cal_del_cancel")])
    return InlineKeyboardMarkup(rows)


def calendar_buttons() -> InlineKeyboardMarkup:
    rows = []
    names = list(cal.CALENDARS.keys())
    for i in range(0, len(names), 2):
        row = []
        for name in names[i:i+2]:
            emoji = cal.CALENDAR_EMOJI.get(name, "📅")
            row.append(InlineKeyboardButton(f"{emoji} {name}", callback_data=f"cal_pick_{name}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


PLAN_GROUPS = {
    "סירקין":     ["ד-ו", "ג", "א-ב", "גן חובה", "ז- בוגרים"],
    "חגור":       ["ד-ח", "א-ג", "גנים"],
    "נווה ירק":   ["גנים", "ג-ו", "א-ב"],
    "אהרונוביץ":  ["א-ה"],
    "איפון פייט": ["ב-ד", "ה-ז"],
    "פונקציונלי": ["ז-ח", 'ט-י"ב'],
    "נבחרת":      ["נבחרת"],
}

def _branch_buttons(active_callback_prefix: str = "mg_branch") -> list:
    """Return branch button rows with next training day marker."""
    from datetime import date as _d, timedelta as _td
    today = _d.today()
    rows = []
    for b in tp.BRANCH_TABS:
        next_days = [today + _td(days=i) for i in range(7)
                     if b in ws.branches_for_date(today + _td(days=i))]
        marker = f" ({ws.day_name(next_days[0])} {next_days[0].day}/{next_days[0].month})" if next_days else ""
        rows.append([InlineKeyboardButton(f"{b}{marker}", callback_data=f"{active_callback_prefix}|{b}")])
    rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
    return rows

def _date_buttons(branch: str) -> list:
    """Return date button rows for next 5 training dates of a branch."""
    from datetime import date as _d
    today = _d.today()
    btns = []
    for d in ws.next_training_dates(branch, n=5):
        diff = (d - today).days
        prefix = "היום" if diff == 0 else "מחר" if diff == 1 else ws.day_name(d)
        btns.append(InlineKeyboardButton(f"{prefix} {d.day}/{d.month}", callback_data=f"mg_date|{d.isoformat()}"))
    rows = [btns[i:i+2] for i in range(0, len(btns), 2)]
    rows.append([InlineKeyboardButton("🔄 שנה סניף", callback_data="mg_change_branch"),
                 InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
    return rows

async def _plan_offer_save(update, user_id: str, plan_text: str, branch, plan_date):
    """
    Offer to save a training plan. Shows minimum buttons needed:
    - If branch+date known → straight to save
    - If only branch known → date buttons
    - If nothing → branch buttons
    """
    from datetime import date as _d
    ss = {"step": "mg_pick_branch", "text": plan_text, "groups": []}

    if branch and plan_date:
        # Both known — confirm directly
        ss["branch"] = branch
        ss["plan_date"] = plan_date.isoformat()
        from training_plans import sheets_sessions as _  # not used, just typing note
        import training_plans as _tp
        sched_groups = ws.groups_for_branch_on_date(branch, plan_date)
        group_names = ", ".join(g["name"] for g in sched_groups) if sched_groups else "לא ידוע"
        from bot import sheets_sessions  # self-reference trick avoided — use global below
        pass

    if branch and plan_date and ws.groups_for_branch_on_date(branch, plan_date):
        # Build preview of what will be written
        preview = tp.preview_plan(branch, plan_date, plan_text)
        day_he = ws.day_name(plan_date)
        sheets_sessions[user_id] = {**ss, "branch": branch, "step": "mg_pick_date",
                                     "plan_date": plan_date.isoformat()}
        lines = [f"💾 *תצוגה מקדימה — {branch} | {day_he} {plan_date.day}/{plan_date.month}*\n"]
        if preview:
            for g in preview:
                time_str = f" ({g['time']})" if g.get("time") else ""
                lines.append(f"*{g['group']}*{time_str}:")
                if g["rows"]:
                    for rt, val in g["rows"]:
                        lines.append(f"  {rt}: {val}")
                else:
                    lines.append("  ⚠️ אין תוכן")
                lines.append("")
        else:
            lines.append("⚠️ לא נמצאו קבוצות לשמירה")
        await update.message.reply_text(
            "\n".join(lines).strip(),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"✅ שמור עכשיו",
                                      callback_data=f"mg_date|{plan_date.isoformat()}"),
                 InlineKeyboardButton("🔄 שנה סניף", callback_data="mg_change_branch")],
                [InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")],
            ])
        )
    elif branch and branch in tp.BRANCH_TABS:
        sheets_sessions[user_id] = {**ss, "branch": branch, "step": "mg_pick_date"}
        await update.message.reply_text(
            f"💾 *שמור תוכנית — {branch}*\n\nלאיזה תאריך?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(_date_buttons(branch))
        )
    else:
        sheets_sessions[user_id] = ss
        await update.message.reply_text(
            "💾 *שמור תוכנית*\n\nאיזה סניף?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(_branch_buttons())
        )


def cancel_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")]])


def with_cancel(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """Add cancel button to existing markup."""
    rows = list(markup.inline_keyboard) + [[InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")]]
    return InlineKeyboardMarkup(rows)


def plan_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💾 שמור ישירות בגיליון", callback_data="save_direct")],
        [
            InlineKeyboardButton("✅ אשר וייצר CSV", callback_data="approve"),
            InlineKeyboardButton("✏️ שנה משהו", callback_data="edit"),
        ],
        [
            InlineKeyboardButton("🔄 תוכנית חלופית", callback_data="alternative"),
        ],
    ])


def approved_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💾 שמור בגיליון תוכניות", callback_data="menu_plan_save")],
    ])


async def send_long(update: Update, text: str, reply_markup=None, parse_mode=None):
    chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
    for i, chunk in enumerate(chunks):
        markup = reply_markup if i == len(chunks) - 1 else None
        await update.message.reply_text(chunk, reply_markup=markup, parse_mode=parse_mode)


CORRECTION_TRIGGERS = ("לא זה", "לא נכון", "תיקון:", "שגוי", "טעית", "תיקן:", "זה לא מה ש",
                       "לא רציתי", "לא ביקשתי", "לא כך", "לא ככה", "תשנה", "תתקן")

async def call_claude(user_id: str, user_content: str, image_b64=None) -> str:
    # Detect corrections and save them
    if any(t in user_content for t in CORRECTION_TRIGGERS):
        hist = get_history(user_id)
        last_bot = next((m["content"] for m in reversed(hist) if m["role"] == "assistant"), "")
        correction_entry = f"המשתמש תיקן: '{user_content[:120]}' — תשובת הבוט הקודמת הייתה: '{last_bot[:120]}'"
        append_correction(correction_entry)

    if image_b64:
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
            {"type": "text", "text": user_content or "מה יש בתמונה? תסביר בהקשר של ג'ודו / ניהול מועדון."},
        ]
        append_history(user_id, "user", f"[תמונה] {user_content}")
    else:
        content = user_content
        append_history(user_id, "user", user_content)

    msgs = get_history(user_id)[:-1] if image_b64 else get_history(user_id)
    if image_b64:
        msgs = get_history(user_id)[:-1] + [{"role": "user", "content": content}]

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=build_system_prompt(),
        messages=msgs,
    )
    reply = response.content[0].text
    append_history(user_id, "assistant", reply)
    return reply


async def deliver_csv(context, chat_id: str, reply_text: str, csv_content: str):
    date_str = datetime.now(IL_TZ).strftime("%Y-%m-%d")
    filename = f"training_{date_str}.csv"
    Path(filename).write_text(csv_content, encoding="utf-8-sig")

    text_part = reply_text[:reply_text.index("```csv")].strip() if "```csv" in reply_text else reply_text
    if text_part:
        await context.bot.send_message(chat_id=chat_id, text=text_part)

    await context.bot.send_document(
        chat_id=chat_id,
        document=open(filename, "rb"),
        filename=filename,
        caption="📊 תוכנית האימון — להדבקה ב-Google Sheets",
        reply_markup=approved_buttons(),
    )


def _hdr(text: str) -> list:
    """Category header row — non-clickable separator button."""
    return [InlineKeyboardButton(text, callback_data="noop")]


def main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([

        # ── יומן ──
        _hdr("━━━━  📅 יומן  ━━━━"),
        [
            InlineKeyboardButton("📅 היום",   callback_data="menu_today"),
            InlineKeyboardButton("📅 מחר",    callback_data="menu_tomorrow"),
        ],
        [
            InlineKeyboardButton("📅 השבוע",  callback_data="menu_week"),
            InlineKeyboardButton("📅 החודש",  callback_data="menu_month"),
        ],
        [InlineKeyboardButton("➕ הוסף אירוע ליומן", callback_data="menu_cal_add")],

        # ── נוכחות ──
        _hdr("━━━━  ✅ נוכחות  ━━━━"),
        [
            InlineKeyboardButton("✅ סמן נוכחות",   callback_data="menu_attendance"),
            InlineKeyboardButton("📊 דוח נוכחות",   callback_data="menu_absence_report"),
        ],

        # ── תוכנית אימון ──
        _hdr("━━━━  🥋 תוכנית אימון  ━━━━"),
        [
            InlineKeyboardButton("🥋 בנה תוכנית",         callback_data="menu_plan"),
            InlineKeyboardButton("📅 יום מלא לסניף",      callback_data="menu_fullday"),
        ],
        [
            InlineKeyboardButton("💾 שמור תוכנית",        callback_data="menu_plan_save"),
            InlineKeyboardButton("📚 ארכיון תוכניות",     callback_data="menu_archive"),
        ],

        # ── תשלומים והורים ──
        _hdr("━━━━  💰 תשלומים והורים  ━━━━"),
        [
            InlineKeyboardButton("💰 מי לא שילם",        callback_data="menu_unpaid"),
            InlineKeyboardButton("📱 הודעות להורים",      callback_data="menu_parent_msgs"),
        ],
        [InlineKeyboardButton("📧 בדוק מיילים תשלום",   callback_data="menu_check_emails")],

        # ── גיליונות ──
        _hdr("━━━━  📂 גיליונות  ━━━━"),
        [
            InlineKeyboardButton("📂 פתח גיליון",        callback_data="menu_open_sheet"),
            InlineKeyboardButton("🎨 עיצוב גיליונות",    callback_data="menu_design"),
        ],
        [InlineKeyboardButton("🧹 נקה עמודות ריקות",     callback_data="menu_cleanup")],

        # ── פרויקטים ──
        _hdr("━━━━  🏕 פרויקטים  ━━━━"),
        [
            InlineKeyboardButton("🏕 מחנה קיץ",   callback_data="menu_camp"),
            InlineKeyboardButton("🌙 לילה יפני",   callback_data="menu_lyla"),
        ],
        [
            InlineKeyboardButton("🏆 תחרויות",    callback_data="menu_competitions"),
            InlineKeyboardButton("📝 הרשמה",       callback_data="menu_open_sheet"),
        ],

        # ── נוסף ──
        _hdr("━━━━  🥇 נוסף  ━━━━"),
        [
            InlineKeyboardButton("🥇 חגורות",      callback_data="menu_belts"),
            InlineKeyboardButton("📊 סטטיסטיקות", callback_data="menu_stats"),
        ],
        [InlineKeyboardButton("📱 אנשי קשר",       callback_data="menu_contacts")],
    ])


def attendance_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔵 סירקין",     callback_data="menu_att_סירקין"),
            InlineKeyboardButton("🟢 חגור",       callback_data="menu_att_חגור"),
        ],
        [
            InlineKeyboardButton("🟡 נווה ירק",   callback_data="menu_att_נווה ירק"),
            InlineKeyboardButton("🟣 אהרונוביץ",  callback_data="menu_att_אהרונוביץ"),
        ],
        [InlineKeyboardButton("🔙 חזרה",          callback_data="menu_back")],
    ])


def belts_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 הודעה להורה אחרי מבחן", callback_data="menu_belt_msg")],
        [
            InlineKeyboardButton("💳 לינק תשלום",   callback_data="menu_belt_pay"),
            InlineKeyboardButton("🌐 פורטל הכנה",   callback_data="menu_belt_portal"),
        ],
        [InlineKeyboardButton("🔙 חזרה",             callback_data="menu_back")],
    ])


def _sheet_url(sheet_id: str) -> str:
    # Deep link opens Google Sheets app directly instead of browser
    return f"googledrive://open?id={sheet_id}"

SHEET_LINKS = {
    # נוכחות
    "נוכחות סירקין":     _sheet_url("1L0mcnpBPW4_3nsxaMy3EunQuOHPjWejvL1Wb6SGzltQ"),
    "נוכחות חגור":       _sheet_url("18p087VLNCRqPOhGbDzUeEg4YIHatiCfSc7v8NVFEPHA"),
    "נוכחות נווה ירק":   _sheet_url("1_J1H0q4-RGy9rH0wyhwfv-47K-uKxiHtbI-D2RoVVOU"),
    "נוכחות אהרונוביץ":  _sheet_url("1MAN8_OnQRBeiznYMvGa57GHU-xz-MErgFkkNOV_Ms8E"),
    "נוכחות פונקציונלי": _sheet_url("1LYqia2ESkLY0HD8QA0vkg1xxqLI5qx0nY9CVVj5MGGY"),
    # תוכניות
    "תוכניות אימון":     _sheet_url("1hi073ueyzdzEjzhP6a3ZgTPpeZDNzH2g2rKPj-L8a6I"),
    "תוכניות 24-25":     _sheet_url("1TTQCzEB-8aw4qrQDsh83IfMKHP7_kSUiLFLTkYIm6a4"),
    # אירועים ופרויקטים
    "מחנה קיץ":          _sheet_url("1lDULmVEYkbbASAdG2MKiozoV1gzsYQ_P-sw_CyilhyE"),
    "לילה יפני":         _sheet_url("1srujIboIUR3D0WQ9z1tHB9_d7jxs3Heoqz2KlwGLbdA"),
    "תחרויות":           _sheet_url("1SaUURPE3a2GgmYRtCTcr7zSUr_EbjeBFEYkk2Nwilow"),
    "הרשמה לחוג":        _sheet_url("1Mm9kbR59NYZ7_9ZskjRzRAmPpXpehJ6tzA0t1kR0_RI"),
    "תשלומים":           _sheet_url("1hzkQZhmtIPL2S11Z399OmJik3pqKyOQsFp33tTNij5o"),
}


def sheets_links_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 סירקין",           url=SHEET_LINKS["נוכחות סירקין"]),
            InlineKeyboardButton("📋 חגור",             url=SHEET_LINKS["נוכחות חגור"]),
        ],
        [
            InlineKeyboardButton("📋 נווה ירק",         url=SHEET_LINKS["נוכחות נווה ירק"]),
            InlineKeyboardButton("📋 אהרונוביץ",        url=SHEET_LINKS["נוכחות אהרונוביץ"]),
        ],
        [
            InlineKeyboardButton("📋 פונקציונלי",       url=SHEET_LINKS["נוכחות פונקציונלי"]),
            InlineKeyboardButton("🗓 תוכניות אימון",    url=SHEET_LINKS["תוכניות אימון"]),
        ],
        [
            InlineKeyboardButton("🏕 מחנה קיץ",         url=SHEET_LINKS["מחנה קיץ"]),
            InlineKeyboardButton("🌸 לילה יפני",        url=SHEET_LINKS["לילה יפני"]),
        ],
        [
            InlineKeyboardButton("🏆 תחרויות",          url=SHEET_LINKS["תחרויות"]),
            InlineKeyboardButton("📝 הרשמה לחוג",       url=SHEET_LINKS["הרשמה לחוג"]),
        ],
        [
            InlineKeyboardButton("💰 תשלומים",          url=SHEET_LINKS["תשלומים"]),
        ],
        [InlineKeyboardButton("🔙 חזרה",                callback_data="menu_back")],
    ])


async def show_main_menu(update, text="👋 שלום טופז! מה תרצה לעשות?"):
    markup = main_menu_markup()
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup)
    else:
        await update.message.reply_text(text, reply_markup=markup)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_main_menu(update)


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_main_menu(update)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # New student text flow takes top priority
    if await handle_new_student_text(update, context):
        return

    # Invoice4u payment sync unknown-name flow
    if await handle_inv4u_text(update, context):
        return

    # Sheets (camp/lyla) text flow
    if await handle_sheets_text(update, context):
        return

    # Calendar task flow
    if await handle_calendar(update, context):
        return

    # Attendance flow
    if await handle_attendance(update, context):
        return

    user_text = update.message.text.strip()
    user_id = str(update.effective_user.id)

    # ── אישור / שמירה מחדש — מ-pending_plans ────────────────────────────────────
    SAVE_TRIGGERS = ("לא שמר", "לא נשמר", "תשמור", "שמור עכשיו", "לשמור", "שמור את זה",
                     "מאשר", "אשר", "כן שמור", "שמור", "סבבה")
    _plan_data = pending_plans.get(user_id)
    if any(t in user_text for t in SAVE_TRIGGERS) and _plan_data:
        plan_data = _plan_data
        plan_text = plan_data.get("reply", "") if isinstance(plan_data, dict) else str(plan_data)
        original_text = plan_data.get("original", "") if isinstance(plan_data, dict) else ""
        # prefer stored branch/date, then detect from original, then from reply
        branch = (plan_data.get("branch") if isinstance(plan_data, dict) else None)
        plan_date_iso = (plan_data.get("plan_date") if isinstance(plan_data, dict) else None)
        from datetime import date as _dt
        plan_date = _dt.fromisoformat(plan_date_iso) if plan_date_iso else None
        if not branch or not plan_date:
            b2, d2 = tp.detect_branch_and_date(original_text)
            branch = branch or b2
            plan_date = plan_date or d2
        if not branch or not plan_date:
            b3, d3 = tp.detect_branch_and_date(plan_text)
            branch = branch or b3
            plan_date = plan_date or d3
        await _plan_offer_save(update, user_id, plan_text, branch, plan_date)
        return

    # ── Training plan detection — user sends plan directly ──────────────────────
    PLAN_KEYWORDS = ("חימום", "תרגול", "קרבות", "רנדורי", "משחק", "כוח", "גאורגי", "נושא")
    is_plan_text = (
        sum(1 for k in PLAN_KEYWORDS if k in user_text) >= 2
        and not sheets_sessions.get(user_id)
        and len(user_text) > 20
    )
    if is_plan_text:
        branch, plan_date = tp.detect_branch_and_date(user_text)
        pending_plans[user_id] = {"reply": user_text, "original": user_text}
        save_json(PENDING_FILE, pending_plans)
        await _plan_offer_save(update, user_id, user_text, branch, plan_date)
        return

    # ── Plan sheet edit — natural language modification ───────────────────────────
    PLAN_EDIT_TRIGGERS = (
        "תשנה את", "תעדכן את", "תוסיף", "תחליף את", "הוסף", "שנה את",
        "תעדכן בגיליון", "תשמור בגיליון", "עדכן בגיליון", "שמור לגיליון",
    )
    PLAN_EDIT_CONTEXT = ("חימום", "תרגול", "קרבות", "רנדורי", "משחק", "כוח", "אימון", "תוכנית")
    if (any(t in user_text for t in PLAN_EDIT_TRIGGERS)
            and any(c in user_text for c in PLAN_EDIT_CONTEXT)
            and not sheets_sessions.get(user_id)):
        sheets_sessions[user_id] = {"step": "plan_edit_who", "edit_text": user_text}
        branch_rows = []
        for b in tp.BRANCH_TABS:
            branch_rows.append([InlineKeyboardButton(b, callback_data=f"pe_branch|{b}")])
        branch_rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
        await update.message.reply_text(
            "✏️ *עדכון תוכנית בגיליון*\n\nאיזה סניף?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(branch_rows),
        )
        return

    # Belt ceremony detection — any mention triggers calendar flow
    BELT_TRIGGERS = ("טקס חגורה", "טקס מעבר", "מעבר חגורה", "עבר חגורה", "עברה חגורה",
                     "עבר מבחן", "עברה מבחן", "עשה מבחן", "עשתה מבחן", "מבחן חגורה")
    if any(t in user_text for t in BELT_TRIGGERS) and "belt_msg" not in str(sheets_sessions.get(user_id, {})):
        sheets_sessions[user_id] = {"step": "belt_msg_details"}
        await update.message.reply_text(
            "🎌 נראה שמדובר בטקס חגורה!\n\n"
            "שלח לי את הפרטים כדי שאכין הודעה ואוסיף ליומן:\n"
            "*שם, צבע חגורה, יום הטקס, סניף, קבוצה* (ואם יש — קישור לסרטון)\n\n"
            "לדוגמה: `מתן שפר, ירוקה, שישי, סירקין, נבחרת, https://...`",
            parse_mode="Markdown",
            reply_markup=cancel_button()
        )
        return

    # "בטל פעולה" → undo last action
    UNDO_TRIGGERS = ("תבטל את זה", "תבטל פעולה", "בטל פעולה", "תבטל מה שעשית",
                     "תחזיר", "תחזיר אחורה", "undo", "אני רוצה לבטל")
    if any(t in user_text for t in UNDO_TRIGGERS):
        history = action_history.get(user_id, [])
        if not history:
            await update.message.reply_text("❌ אין פעולה לביטול.")
            return
        last = history[-1]
        await update.message.reply_text(
            f"↩️ *הפעולה האחרונה:*\n{last['description']}\n\nלבטל?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ כן, בטל", callback_data="undo_last"),
                InlineKeyboardButton("❌ לא", callback_data="cancel_flow"),
            ]])
        )
        return

    # "בטל" → cancel all active sessions
    if user_text in ("בטל", "ביטול", "cancel", "/cancel", "עצור", "חדש", "התחל מחדש"):
        user_id_local = str(update.effective_user.id)
        sheets_sessions.pop(user_id_local, None)
        calendar_sessions.pop(user_id_local, None)
        pending_plans.pop(user_id_local, None)
        await update.message.reply_text(
            "בסדר, מתחילים מחדש 👍\nבמה אפשר לעזור?",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 תפריט", callback_data="menu_main")]])
        )
        return

    # "תפריט" keyword → show main menu
    if user_text in ("תפריט", "menu", "/menu"):
        await show_main_menu(update)
        return

    # Text triggers for camp/lyla menus
    if any(t in user_text for t in ("מחנה קיץ", "מחנה", "camp")):
        await camp_command(update, context)
        return
    if any(t in user_text for t in ("לילה יפני", "לילה")):
        await lyla_command(update, context)
        return

    # Detect if user pasted a ready-made belt ceremony message back to the bot
    if "אני שמח לעדכן" in user_text and "חגורה" in user_text and "טקס מעבר חגורה" in user_text:
        await update.message.reply_text(
            "✅ ההודעה נראית מוכנה!\n\n"
            "📋 פשוט *העתק* אותה ושלח ישירות לקבוצת הוואטסאפ של ההורים.\n\n"
            "אם רוצה להוסיף את הטקס ליומן גוגל — חזור לתפריט ← 🥇 חגורות ← 📝 הודעה להורה ← 📅 הוסף ליומן.",
            parse_mode="Markdown"
        )
        return

    # Build history context from archive
    extra_context = ""
    import training_archive as _arc
    from datetime import date as _today_cls
    detected_branch = next((b for b in tp.BRANCH_TABS if b in user_text), None)
    if detected_branch:
        all_group_names = PLAN_GROUPS.get(detected_branch, [])
        if all_group_names:
            extra_context = _arc.suggest_context_for_claude(detected_branch, all_group_names)
        else:
            for grp in ["גנים", "א-ב", "א-ג", "ב-ד", "ג", "ג-ו", "ד-ו", "ד-ח", "ה-ז", "ז-ח", "ז- בוגרים", 'ט-י"ב', "נבחרת"]:
                if grp in user_text:
                    extra_context = _arc.format_history(detected_branch, grp, n=3)
                    break

    # Inject live data when relevant
    data_context = _build_data_context(user_text)

    full_content = user_text
    if data_context:
        full_content = f"[נתונים]\n{data_context}\n\n{user_text}"
    elif extra_context:
        full_content = f"{user_text}\n\n[הקשר אוטומטי]\n{extra_context}"

    # Check if user is typing a message for a WhatsApp group
    pending_group = context.bot_data.get("wa_pending_group")
    if pending_group:
        del context.bot_data["wa_pending_group"]
        await wa_send_with_approval(
            context,
            chat_id=str(update.effective_chat.id),
            phone=pending_group["id"],  # group JID
            recipient_name=f"קבוצה: {pending_group['name']}",
            message=user_text
        )
        return

    await update.message.chat.send_action("typing")

    try:
        reply = await call_claude(user_id, full_content)
    except Exception as e:
        log.error("Claude API error: %s", e)
        conversation_log.log_conversation(user_text, "❌ שגיאת Claude", action="error", success=False)
        await update.message.reply_text("❌ שגיאה בתקשורת עם Claude. נסה שוב.")
        return

    # Log conversation for Cowork sync
    try:
        action_tag = "תוכנית אימון" if any(k in reply for k in ("חימום:", "תרגול:", "קרבות:")) else "תשובה כללית"
        conversation_log.log_conversation(user_text, reply, action=action_tag)
    except Exception as _cle:
        log.debug(f"conv log skipped: {_cle}")

    # if Claude already returned a CSV (skipped the proposal step)
    if "```csv" in reply:
        csv_start = reply.index("```csv") + 6
        csv_end = reply.index("```", csv_start)
        csv_content = reply[csv_start:csv_end].strip()
        await deliver_csv(context, update.effective_chat.id, reply, csv_content)
    else:
        PLAN_KEYWORDS = ("חימום", "תרגול", "קרבות", "רנדורי", "כוח", "סיום",
                         "EMOM", "E2MOM", "E1MOM", "AMRAP", "טבאטה", "Tabata",
                         "שליחים", "ג'ונגל", "ביסט", "עיר הקרב", "ג'ודופונג",
                         "Bench", "Pull-up", "Rope Climb", "Box Jump")
        # זיהוי מחמיר: חייב מבנה מסודר עם נקודותיים (חימום: / תרגול:) — לא רק מילות מפתח
        PLAN_STRUCTURE = ("חימום:", "תרגול:", "קרבות:", "משחק:", "כוח:", "רנדורי:")
        NEGATIVE_SIGNALS = ("באג", "צריך לתקן", "שגיאה בקוד", "הפונקציה", "לשמור לגיליון?",
                            "תיקון", "הבוט כתב", "במקום להציג", "ארגומנטים")
        has_structure = sum(1 for k in PLAN_STRUCTURE if k in reply) >= 2
        has_negative  = any(n in reply for n in NEGATIVE_SIGNALS)
        is_training_plan = has_structure and not has_negative

        if is_training_plan:
            # Detect branch+date from original request and store — so save works even
            # if user types "מאשר" or "שמור" without clicking the button
            _b, _d = tp.detect_branch_and_date(user_text)
            pending_plans[user_id] = {
                "reply": reply,
                "original": user_text,
                "branch": _b or "",
                "plan_date": _d.isoformat() if _d else "",
            }
            save_json(PENDING_FILE, pending_plans)

        if is_training_plan:
            _b = pending_plans[user_id].get("branch", "") if isinstance(pending_plans.get(user_id), dict) else ""
            _d_iso = pending_plans[user_id].get("plan_date", "") if isinstance(pending_plans.get(user_id), dict) else ""
            if _b and _d_iso:
                from datetime import date as _date_cls
                _d = _date_cls.fromisoformat(_d_iso)
                _day_he = ws.day_name(_d)
                save_label = f"💾 שמור — {_b} | {_day_he} {_d.day}/{_d.month}"
                save_cb    = f"plan_save_quick|{_b}|{_d_iso}"
            else:
                save_label = "💾 שמור בגיליון"
                save_cb    = "menu_plan_save"
            save_plan_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton(save_label, callback_data=save_cb)],
                [InlineKeyboardButton("✏️ ערוך תוכנית", callback_data="plan_edit_current")],
            ])
        else:
            save_plan_markup = None

        chunks = [reply[i:i+4096] for i in range(0, len(reply), 4096)]
        for i, chunk in enumerate(chunks):
            markup = save_plan_markup if (i == len(chunks) - 1 and is_training_plan) else None
            await update.message.reply_text(chunk, reply_markup=markup)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    action = query.data
    if action == "noop":
        await query.answer()
        return

    # ─── Plan edit callbacks ───
    if action.startswith("pe_branch|"):
        await query.answer()
        branch = action.split("|", 1)[1]
        ss = sheets_sessions.get(user_id, {})
        ss["branch"] = branch
        ss["step"] = "plan_edit_group"
        sheets_sessions[user_id] = ss
        groups = PLAN_GROUPS.get(branch, [])
        rows = [[InlineKeyboardButton(g, callback_data=f"pe_group|{g}")] for g in groups]
        rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
        await query.edit_message_text(
            f"✅ {branch}\n\nאיזו קבוצה לעדכן?",
            reply_markup=InlineKeyboardMarkup(rows)
        )
        return

    if action.startswith("pe_group|"):
        await query.answer()
        group = action.split("|", 1)[1]
        ss = sheets_sessions.get(user_id, {})
        ss["group"] = group
        ss["step"] = "plan_edit_date"
        sheets_sessions[user_id] = ss
        branch_for_dates = ss.get("branch", "")
        from datetime import date as _date
        today = _date.today()
        dates = ws.next_training_dates(branch_for_dates, n=5) if branch_for_dates else []
        date_btns = []
        for d in dates:
            diff = (d - today).days
            prefix = "היום" if diff == 0 else "מחר" if diff == 1 else ws.day_name(d)
            date_btns.append(InlineKeyboardButton(
                f"{prefix} {d.day}/{d.month}", callback_data=f"pe_date|{d.isoformat()}"
            ))
        rows = [date_btns[i:i+2] for i in range(0, len(date_btns), 2)]
        rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
        await query.edit_message_text(
            f"✅ {ss['branch']} — {group}\n\n5 תאריכי אימון קרובים:",
            reply_markup=InlineKeyboardMarkup(rows)
        )
        return

    if action.startswith("pe_date|"):
        await query.answer()
        from datetime import date as _date
        plan_date = _date.fromisoformat(action.split("|", 1)[1])
        ss = sheets_sessions.get(user_id, {})
        ss["plan_date"] = plan_date.isoformat()
        ss["step"] = "plan_edit_content"
        sheets_sessions[user_id] = ss
        await query.edit_message_text(
            f"✅ {ss['branch']} — {ss['group']} — {plan_date.day}/{plan_date.month}\n\n"
            f"שלח את תוכן האימון (שורות, כדורים, או טקסט חופשי).\n"
            f"הבוט יסדר אוטומטית לחימום / תרגול / קרבות / משחק / כוח.",
        )
        return

    # ─── Multi-group plan callbacks ───
    if action == "mg_change_branch":
        await query.answer()
        ss = sheets_sessions.get(user_id, {})
        ss["step"] = "mg_pick_branch"
        sheets_sessions[user_id] = ss
        today_branches = ws.today_branches()
        rows = []
        for b in tp.BRANCH_TABS:
            marker = " ✓" if b in today_branches else ""
            rows.append([InlineKeyboardButton(f"{b}{marker}", callback_data=f"mg_branch|{b}")])
        rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
        await query.edit_message_text(
            "בחר סניף (✓ = מתאמן היום):",
            reply_markup=InlineKeyboardMarkup(rows)
        )
        return

    # ─── Undo last action ───
    if action == "undo_last":
        await query.answer()
        history = action_history.get(user_id, [])
        if not history:
            await query.edit_message_text("❌ אין פעולה לביטול.")
            return
        last = history[-1]
        undo_type = last["type"]
        undo_data = last["undo_data"]

        try:
            if undo_type == "payment_update":
                # Restore previous payment value
                payments_sheet.update_payment(
                    undo_data["row"], undo_data["month"], undo_data["prev_value"]
                )
                action_history[user_id].pop()
                await query.edit_message_text(
                    f"↩️ *בוטל!* תשלום {undo_data['student']} — {undo_data['month']} "
                    f"שוחזר ל: {undo_data['prev_value'] or 'ריק'}",
                    parse_mode="Markdown"
                )
            elif undo_type == "plan_save":
                # Clear the plan cell (write empty string)
                import training_plans as _tp
                from datetime import date as _date
                pd = _date.fromisoformat(undo_data["plan_date"])
                _tp.save_plan_to_sheet(
                    undo_data["branch"], undo_data["group"], pd,
                    [""] * 6
                )
                action_history[user_id].pop()
                await query.edit_message_text(
                    f"↩️ *בוטל!* תוכנית {undo_data['branch']} — {undo_data['group']} "
                    f"נמחקה מ-{pd.day}/{pd.month}",
                    parse_mode="Markdown"
                )
            elif undo_type == "calendar_event":
                cal.delete_event(undo_data["event_index"])
                action_history[user_id].pop()
                await query.edit_message_text(
                    f"↩️ *בוטל!* אירוע יומן נמחק: {undo_data['title']}",
                    parse_mode="Markdown"
                )
            else:
                await query.edit_message_text(f"⚠️ לא ניתן לבטל פעולה מסוג: {undo_type}")
        except Exception as e:
            await query.edit_message_text(f"❌ שגיאה בביטול: {e}")
        return

    if action.startswith("mg_branch|"):
        await query.answer()
        branch = action.split("|", 1)[1]
        ss = sheets_sessions.get(user_id, {})
        ss["branch"] = branch
        ss["step"] = "mg_pick_date"
        sheets_sessions[user_id] = ss
        from datetime import date as _date
        dates = ws.next_training_dates(branch, n=5)
        today = _date.today()
        date_options = []
        for d in dates:
            diff = (d - today).days
            prefix = "היום" if diff == 0 else "מחר" if diff == 1 else ws.day_name(d)
            date_options.append(InlineKeyboardButton(
                f"{prefix} {d.day}/{d.month}", callback_data=f"mg_date|{d.isoformat()}"
            ))
        rows = [date_options[i:i+2] for i in range(0, len(date_options), 2)]
        rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
        groups_str = ", ".join(g["group"] for g in ss.get("groups", []))
        await query.edit_message_text(
            f"✅ סניף: *{branch}*\nקבוצות: {groups_str}\n\n5 תאריכי אימון קרובים:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if action.startswith("mg_force|"):
        await query.answer()
        from datetime import date as _date
        date_str = action.split("|", 1)[1]
        plan_date = _date.fromisoformat(date_str)
        ss = sheets_sessions.pop(user_id, {})
        branch = ss.get("branch", "")
        plan_text = ss.get("text", "")
        sched_groups = ws.groups_for_branch_on_date(branch, plan_date)
        n_groups = len(sched_groups) if sched_groups else 1
        await query.edit_message_text(f"⏳ שומר {n_groups} קבוצות לגיליון — {branch} {plan_date.day}/{plan_date.month}...")
        try:
            result = tp.save_full_day(branch, plan_date, plan_text)
            await query.message.reply_text(f"✅ *נשמר!*\n\n{result}", parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📋 פתח גיליון",
                        url="https://docs.google.com/spreadsheets/d/1hi073ueyzdzEjzhP6a3ZgTPpeZDNzH2g2rKPj-L8a6I/edit"),
                    InlineKeyboardButton("↩️ בטל", callback_data="undo_last"),
                ]]))
        except Exception as e:
            await query.message.reply_text(f"❌ שגיאה בשמירה: {e}")
        return

    if action.startswith("mg_date|"):
        await query.answer()
        from datetime import date as _date, timedelta as _td
        date_str = action.split("|", 1)[1]
        plan_date = _date.fromisoformat(date_str)
        ss = sheets_sessions.get(user_id, {})
        branch = ss.get("branch", "")
        groups = ss.get("groups", [])

        # Validate: does this branch train on the selected date?
        branches_that_day = ws.branches_for_date(plan_date)
        if branch and branch not in branches_that_day:
            # Wrong branch for this day — offer correction
            day_he = ws.day_name(plan_date)
            if branches_that_day:
                correct = branches_that_day[0]
                # Auto-correct if only one branch trains that day
                if len(branches_that_day) == 1:
                    ss["branch"] = correct
                    sheets_sessions[user_id] = ss
                    branch = correct
                    await query.edit_message_text(
                        f"⚠️ *תוקן אוטומטית:* ביום {day_he} {plan_date.day}/{plan_date.month} "
                        f"מתאמן *{correct}*, לא {branch}\n\n⏳ שומר...",
                        parse_mode="Markdown"
                    )
                else:
                    # Multiple branches that day — ask user
                    ss["step"] = "mg_pick_branch"
                    sheets_sessions[user_id] = ss
                    rows = [[InlineKeyboardButton(b, callback_data=f"mg_branch|{b}")]
                            for b in branches_that_day]
                    rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
                    await query.edit_message_text(
                        f"⚠️ *{branch}* לא מתאמן ביום {day_he} {plan_date.day}/{plan_date.month}.\n"
                        f"מי מתאמן ביום הזה?",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(rows)
                    )
                    return
            else:
                await query.edit_message_text(
                    f"⚠️ לא מוגדר אימון ביום {day_he} {plan_date.day}/{plan_date.month}.\n"
                    f"רוצה לשמור בכל זאת ל*{branch}*?",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ כן, שמור", callback_data=f"mg_force|{date_str}"),
                        InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow"),
                    ]])
                )
                return

        plan_text = ss.get("text", "")
        sched_groups = ws.groups_for_branch_on_date(branch, plan_date)
        n_groups = len(sched_groups) if sched_groups else len(groups)
        sheets_sessions.pop(user_id, None)

        # Build preview for verification later
        preview = tp.preview_plan(branch, plan_date, plan_text)

        await query.edit_message_text(
            f"⏳ שומר {n_groups} קבוצות לגיליון — {branch} {plan_date.day}/{plan_date.month}..."
        )
        try:
            result = tp.save_full_day(branch, plan_date, plan_text)
            record_action(user_id, "plan_save",
                f"תוכנית {branch} {plan_date.day}/{plan_date.month} — כל הקבוצות",
                {"branch": branch, "group": "all", "plan_date": plan_date.isoformat()}
            )

            # Verify what was actually written
            verify = tp.verify_plan_saved(branch, plan_date, preview)
            if verify:
                ver_lines = ["📋 *מה נכתב בפועל:*"]
                all_ok = True
                for v in verify:
                    if v["written"]:
                        ver_lines.append(f"✅ *{v['group']}*: " +
                            " | ".join(val for _, val in v["written"]))
                    else:
                        ver_lines.append(f"⚠️ *{v['group']}*: לא נכתב כלום")
                        all_ok = False
                confirm_text = "\n".join(ver_lines)
            else:
                confirm_text = result
                all_ok = "✅" in result

            from training_plans import SPREADSHEET_ID
            sheet_url = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit"
            await query.message.reply_text(
                f"{'✅' if all_ok else '⚠️'} *{branch} {plan_date.day}/{plan_date.month}*\n\n{confirm_text}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📋 פתח גיליון", url=sheet_url),
                    InlineKeyboardButton("↩️ בטל", callback_data="undo_last"),
                ]])
            )
        except Exception as e:
            await query.message.reply_text(f"❌ שגיאה בשמירה: {e}")
        return

    # ─── Unpaid / payments callbacks ───
    if action.startswith("unpaid_month|"):
        await query.answer()
        month = action.split("|", 1)[1]
        await query.message.chat.send_action("typing")
        msg = payments_report.format_unpaid_message(month)
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("📱 הכן הודעת ווטסאפ", callback_data=f"unpaid_wa|{month}"),
        ]])
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=markup)
        return

    if action.startswith("unpaid_wa|"):
        await query.answer()
        month = action.split("|", 1)[1]
        unpaid_map = payments_report.get_unpaid(month)
        students = unpaid_map.get(month, [])
        if not students:
            await query.message.reply_text("✅ אין חייבים!")
            return
        by_club: dict[str, list] = {}
        for s in students:
            by_club.setdefault(s["club"] or "לא ידוע", []).append(s["full_name"])
        wa_lines = [f"שלום,\nתזכורת לתשלום דמי אימון עבור חודש {month}:\n"]
        for club, names in sorted(by_club.items()):
            wa_lines.append(f"📍 {club}:")
            for n in sorted(names):
                wa_lines.append(f"  • {n}")
        wa_lines.append("\nניתן לשלם בקישור: https://private.invoice4u.co.il/Clearing/Invoice4UClearing.aspx?ProductId=4476&mobileApp=true")
        wa_lines.append("\nתודה 🙏 — טופז")
        await query.message.reply_text("\n".join(wa_lines))
        return

    # ─── Payment approval callbacks ───
    if action.startswith("pay_approve|"):
        await query.answer()
        key = action.split("|", 1)[1]
        p = pending_payments.get(key)
        if not p:
            await query.edit_message_text("❌ לא נמצא המידע — אולי כבר טופל.")
            return
        student = p.get("student")
        if not student:
            await query.edit_message_text(
                f"⚠️ לא מצאתי את הספורטאי *{p['student_name']}* בגיליון.\n"
                "עדכן ידנית בגיליון התשלומים.",
                parse_mode="Markdown"
            )
            pending_payments.pop(key, None)
            return
        try:
            prev = payments_sheet.get_month_value(student["row"], p["month"])
            payments_sheet.update_payment(student["row"], p["month"], p["amount"])
            record_action(user_id, "payment_update",
                f"תשלום {student['full_name']} {p['month']} {p['amount']}₪",
                {"row": student["row"], "month": p["month"],
                 "student": student["full_name"], "prev_value": prev}
            )
            await query.edit_message_text(
                f"✅ *עודכן בדוח התשלומים!*\n\n"
                f"• ספורטאי: {student['full_name']}\n"
                f"• חודש: {p['month']}\n"
                f"• סכום: {p['amount']}₪\n"
                f"• מועדון: {student['club']}",
                parse_mode="Markdown",
                reply_markup=undo_button()
            )
        except Exception as e:
            await query.edit_message_text(f"❌ שגיאה בעדכון: {e}")
        pending_payments.pop(key, None)
        return

    if action.startswith("pay_reject|"):
        await query.answer()
        key = action.split("|", 1)[1]
        pending_payments.pop(key, None)
        await query.edit_message_text("❌ נדחה — לא עודכן בדוח.")
        return

    if action.startswith("pay_edit|"):
        await query.answer()
        key = action.split("|", 1)[1]
        p = pending_payments.get(key)
        if not p:
            await query.edit_message_text("❌ לא נמצא המידע.")
            return
        sheets_sessions[user_id] = {"step": "pay_edit_input", "pay_key": key}
        await query.message.reply_text(
            f"✏️ שלח בפורמט: `שם מלא | חודש | סכום`\n"
            f"לדוגמה: `{p['student_name']} | {p['month']} | {p['amount']}`",
            parse_mode="Markdown",
            reply_markup=cancel_button()
        )
        return

    # ── Parent message callbacks ──
    if action.startswith("pm_abs_br|"):
        await query.answer()
        branch = action.split("|", 1)[1]
        ss = sheets_sessions.get(user_id, {})
        name = ss.get("name", "")
        from datetime import date as _d
        date_str = _d.today().strftime("%d/%m/%Y")
        # Count consecutive absences
        log_data = load_json(Path("absence_log.json"), {})
        records = log_data.get(name, [])
        consec = sum(1 for r in reversed(records[-5:]) if r.get("absent"))
        msg = contacts_db.compose_absence_message(name, branch, date_str, consecutive=max(consec, 1))
        sheets_sessions.pop(user_id, None)
        await query.edit_message_text(
            f"📱 *הודעה מוכנה:*\n\n{msg}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 תפריט", callback_data="menu_back")]])
        )
        return

    if action.startswith("pm_pay_br|"):
        await query.answer()
        branch = action.split("|", 1)[1]
        ss = sheets_sessions.get(user_id, {})
        name = ss.get("name", "")
        from datetime import date as _d
        from calendar import month_abbr as _mn
        month_he = ["", "ינואר","פברואר","מרץ","אפריל","מאי","יוני","יולי","אוגוסט","ספטמבר","אוקטובר","נובמבר","דצמבר"]
        month_str = month_he[_d.today().month]
        msg = contacts_db.compose_payment_reminder(name, branch, month_str)
        sheets_sessions.pop(user_id, None)
        await query.edit_message_text(
            f"💰 *תזכורת תשלום מוכנה:*\n\n{msg}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 תפריט", callback_data="menu_back")]])
        )
        return

    # ─── Absent parent messages ───
    if action == "absent_msgs_all":
        await query.answer()
        ss = sheets_sessions.get(user_id, {})
        absent_names = ss.get("absent_names", [])
        branch = ss.get("branch", "")
        date_str = ss.get("date", "")
        if not absent_names:
            await query.message.reply_text("❌ לא נמצאו נעדרים.")
            return
        msg_lines = ["📩 *הודעות לשליחה:*\n"]
        for name in absent_names:
            try:
                # Count consecutive absences
                log_data = load_json(Path("absence_log.json"), {})
                records = log_data.get(name, [])
                consec = sum(1 for r in reversed(records[-5:]) if r.get("absent"))
                txt = contacts_db.compose_absence_message(name, branch, date_str, consecutive=consec)
                msg_lines.append(txt)
                msg_lines.append("─" * 20)
            except Exception:
                msg_lines.append(f"*{name}* — לא נמצא איש קשר")
        sheets_sessions.pop(user_id, None)
        await query.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")
        return

    # ─── Main menu callbacks ───
    if action == "noop":
        await query.answer()
        return

    if action in ("menu_back", "menu_main"):
        await show_main_menu(update)
        await query.answer()
        return

    if action == "menu_today":
        await query.answer()
        await _calendar_query(update, context, "היום")
        return

    if action == "menu_tomorrow":
        await query.answer()
        await _calendar_query(update, context, "מחר")
        return

    if action == "menu_week":
        await query.answer()
        await _calendar_query(update, context, "השבוע")
        return

    if action == "menu_month":
        await query.answer()
        await _calendar_query(update, context, "החודש")
        return

    if action == "menu_cal_add":
        await query.answer()
        await query.edit_message_text(
            "📝 שלח לי מה להוסיף ליומן\nלדוגמה: *מחר ב-10:00 פגישה עם אבא של בועז*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="menu_back")]]),
        )
        return

    if action == "menu_plan":
        await query.answer()
        await query.edit_message_text(
            "🥋 שלח לי בקשה לתוכנית אימון\nלדוגמה: *סירקין יום ב׳, ד-ו וז-בוגרים*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="menu_back")]]),
        )
        return

    if action == "menu_plan_save":
        await query.answer()
        plan_data = pending_plans.get(user_id, {})
        plan_text = plan_data.get("reply", "") if isinstance(plan_data, dict) else str(plan_data)
        original_text = plan_data.get("original", "") if isinstance(plan_data, dict) else ""
        # Detect branch+date from original user request first (more reliable than Claude reply)
        branch, plan_date = tp.detect_branch_and_date(original_text)
        if not branch or not plan_date:
            b2, d2 = tp.detect_branch_and_date(plan_text)
            branch = branch or b2
            plan_date = plan_date or d2
        class _FakeUpdate:
            def __init__(self, msg): self.message = msg
        await _plan_offer_save(_FakeUpdate(query.message), user_id, plan_text, branch, plan_date)
        return

    # ── Quick save — branch and date embedded in callback ──
    if action.startswith("plan_save_quick|"):
        await query.answer()
        parts = action.split("|")
        branch   = parts[1] if len(parts) > 1 else ""
        date_iso = parts[2] if len(parts) > 2 else ""
        plan_data = pending_plans.get(user_id, {})
        plan_text = plan_data.get("reply", "") if isinstance(plan_data, dict) else str(plan_data)
        if not plan_text:
            await query.message.reply_text("❌ לא נמצאה תוכנית לשמירה. שלח את התוכנית מחדש.")
            return
        if not branch or not date_iso:
            class _FakeUpdate2:
                def __init__(self, msg): self.message = msg
            await _plan_offer_save(_FakeUpdate2(query.message), user_id, plan_text, None, None)
            return
        from datetime import date as _date_cls
        plan_date = _date_cls.fromisoformat(date_iso)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"⏳ שומר תוכנית לגיליון — {branch} | {plan_date.day}/{plan_date.month}...")
        try:
            result = tp.save_full_day(branch, plan_date, plan_text)
            record_action(user_id, "plan_save", f"שמירת תוכנית {branch} {plan_date}",
                          {"branch": branch, "plan_date": date_iso})
            await query.message.reply_text(result, parse_mode="Markdown")
        except Exception as e:
            await query.message.reply_text(f"❌ שגיאה בשמירה: {e}")
        return

    # ── Edit current plan (opens branch/date picker for editing) ──
    if action == "plan_edit_current":
        await query.answer()
        plan_data = pending_plans.get(user_id, {})
        branch = plan_data.get("branch", "") if isinstance(plan_data, dict) else ""
        date_iso = plan_data.get("plan_date", "") if isinstance(plan_data, dict) else ""
        if branch and date_iso:
            from datetime import date as _date_cls
            plan_date = _date_cls.fromisoformat(date_iso)
            day_he = ws.day_name(plan_date)
            await query.message.reply_text(
                f"✏️ *עריכת תוכנית — {branch} | {day_he} {plan_date.day}/{plan_date.month}*\n\n"
                "שלח את הטקסט המעודכן של התוכנית (את כל הקבוצות כולן):",
                parse_mode="Markdown",
                reply_markup=cancel_button()
            )
            sheets_sessions[user_id] = {
                "step": "fd_waiting_plan",
                "branch": branch,
                "plan_date": date_iso,
            }
        else:
            await query.message.reply_text(
                "✏️ *עריכת תוכנית*\n\nאיזה סניף ותאריך לערוך?\n"
                "דוגמה: `/edit סירקין 26/6`",
                parse_mode="Markdown",
            )
        return

    # ── Plan wizard — confirm save ──
    if action == "pw_confirm":
        await query.answer()
        ss = sheets_sessions.get(user_id, {})
        await _plan_wizard_save(query.message, user_id, ss)
        sheets_sessions.pop(user_id, None)
        return

    if action == "pw_reedit":
        await query.answer()
        ss = sheets_sessions.get(user_id, {})
        ss["step"] = "pw_waiting_plan"
        sheets_sessions[user_id] = ss
        await query.message.reply_text(
            "✏️ שלח את התוכנית מחדש:",
            reply_markup=cancel_button()
        )
        return

    if action == "menu_design":
        await query.answer()
        await query.edit_message_text(
            "🎨 עיצוב גיליון נוכחות — שלח:\n`/design סירקין ד-ו`\nאו בחר סניף:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔵 סירקין",    callback_data="menu_design_סירקין"),
                 InlineKeyboardButton("🟢 חגור",      callback_data="menu_design_חגור")],
                [InlineKeyboardButton("🟡 נווה ירק",  callback_data="menu_design_נווה ירק"),
                 InlineKeyboardButton("🟣 אהרונוביץ", callback_data="menu_design_אהרונוביץ")],
                [InlineKeyboardButton("🔙 חזרה",      callback_data="menu_back")],
            ]))
        return

    if action.startswith("menu_design_"):
        await query.answer()
        branch = action[len("menu_design_"):]
        msg = await query.edit_message_text(f"🎨 מעצב {branch}...")
        try:
            for group in att.BRANCH_GROUPS.get(branch, []):
                att.apply_sheet_design(branch, group)
            await query.edit_message_text(f"✅ עיצוב הוחל על {branch}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="menu_back")]]))
        except Exception as e:
            await query.edit_message_text(f"❌ שגיאה: {e}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="menu_back")]]))
        return

    if action == "menu_attendance":
        await query.answer()
        await query.edit_message_text("✅ נוכחות — בחר סניף:", reply_markup=attendance_menu_markup())
        return

    if action.startswith("menu_att_"):
        branch = action.replace("menu_att_", "")
        await query.answer()
        await query.message.reply_text(f"✅ מתחיל נוכחות {branch}...")
        # trigger attendance flow directly
        class FakeUpdate:
            def __init__(self, msg): self.message = msg
        fake = FakeUpdate(query.message)
        fake.message._text = f"נוכחות {branch}"
        context.user_data["pending_branch"] = branch
        await start_attendance_session(query.message._bot, str(query.message.chat_id), user_id, branch, "")
        return

    if action == "menu_belts":
        await query.answer()
        await query.message.reply_text("🥇 חגורות — בחר פעולה:", reply_markup=belts_menu_markup())
        return

    if action == "menu_belt_msg":
        await query.answer()
        sheets_sessions[user_id] = {"step": "belt_wizard_name"}
        await query.message.reply_text(
            "🎌 *שם הילד/ה?*",
            parse_mode="Markdown",
            reply_markup=cancel_button()
        )
        return

    # ── Belt wizard — color picker ──
    if action.startswith("bw_color|"):
        await query.answer()
        color = action.split("|", 1)[1]
        ss = sheets_sessions.get(user_id, {})
        ss["belt_color"] = color
        ss["step"] = "belt_wizard_branch"
        sheets_sessions[user_id] = ss
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("סירקין", callback_data="bw_branch|סירקין"),
             InlineKeyboardButton("חגור", callback_data="bw_branch|חגור")],
            [InlineKeyboardButton("נווה ירק", callback_data="bw_branch|נווה ירק"),
             InlineKeyboardButton("אהרונוביץ", callback_data="bw_branch|אהרונוביץ")],
            [InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")],
        ])
        await query.message.reply_text(f"✅ {color}\n\n🏠 *איזה סניף?*", parse_mode="Markdown", reply_markup=markup)
        return

    # ── Belt wizard — branch picker ──
    if action.startswith("bw_branch|"):
        await query.answer()
        branch = action.split("|", 1)[1]
        ss = sheets_sessions.get(user_id, {})
        ss["branch"] = branch
        ss["step"] = "belt_wizard_group"
        sheets_sessions[user_id] = ss

        GROUPS = {
            "סירקין":    [("ד-ו","bw_group|ד-ו"), ("ג","bw_group|ג"), ("א-ב","bw_group|א-ב"),
                          ("גנים","bw_group|גנים"), ("ז-בוגרים","bw_group|ז-בוגרים"), ("נבחרת","bw_group|נבחרת")],
            "חגור":      [("ד-ח","bw_group|ד-ח"), ("א-ג","bw_group|א-ג"), ("גנים","bw_group|גנים")],
            "נווה ירק":  [("גנים","bw_group|גנים"), ("ג-ו","bw_group|ג-ו"), ("א-ב","bw_group|א-ב")],
            "אהרונוביץ": [("א-ה","bw_group|א-ה")],
        }
        rows = []
        for label, cb in GROUPS.get(branch, []):
            rows.append([InlineKeyboardButton(label, callback_data=cb)])
        rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
        await query.message.reply_text(f"✅ {branch}\n\n👥 *איזו קבוצה?*", parse_mode="Markdown",
                                        reply_markup=InlineKeyboardMarkup(rows))
        return

    # ── Belt wizard — group picker ──
    if action.startswith("bw_group|"):
        await query.answer()
        group = action.split("|", 1)[1]
        ss = sheets_sessions.get(user_id, {})
        ss["group"] = group
        branch = ss.get("branch", "")
        sheets_sessions[user_id] = ss

        # Schedule: (branch, group) → list of (day, end_time)
        SCHED = {
            ("סירקין", "ד-ו"):       [("שני", "15:30"), ("חמישי", "15:30")],
            ("סירקין", "ג"):          [("שני", "16:30"), ("חמישי", "16:30")],
            ("סירקין", "א-ב"):        [("שני", "17:15"), ("חמישי", "17:15")],
            ("סירקין", "גנים"):       [("חמישי", "18:00")],
            ("סירקין", "ז-בוגרים"):  [("שני", "19:30"), ("חמישי", "19:30")],
            ("סירקין", "נבחרת"):      [("שישי", "15:00")],
            ("חגור",   "ד-ח"):        [("ראשון", "16:30")],
            ("חגור",   "א-ג"):        [("ראשון", "17:15")],
            ("חגור",   "גנים"):       [("ראשון", "18:00")],
            ("נווה ירק","גנים"):      [("שלישי", "16:45")],
            ("נווה ירק","ג-ו"):       [("שלישי", "17:45")],
            ("נווה ירק","א-ב"):       [("שלישי", "18:30")],
            ("אהרונוביץ","א-ה"):      [("רביעי", "14:50")],
        }
        options = SCHED.get((branch, group), [])

        if len(options) == 1:
            # Only one day — skip day picker
            day, end_time = options[0]
            h, m = map(int, end_time.split(":"))
            total = h * 60 + m - 10
            ceremony_time = f"{total//60:02d}:{total%60:02d}"
            ss["ceremony_day"] = day
            ss["ceremony_time"] = ceremony_time
            ss["step"] = "belt_wizard_link"
            sheets_sessions[user_id] = ss
            await query.message.reply_text(
                f"✅ {group} — יום {day} ב-{ceremony_time}\n\n"
                "📸 *קישור לסרטון המבחן?* (אופציונלי)",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("דלג ➡️", callback_data="bw_link|skip")],
                    [InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")],
                ])
            )
        else:
            # Multiple days — ask which day
            ss["step"] = "belt_wizard_day"
            sheets_sessions[user_id] = ss
            rows = [[InlineKeyboardButton(f"יום {day} ({end_time})", callback_data=f"bw_day|{day}|{end_time}")]
                    for day, end_time in options]
            rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
            await query.message.reply_text(
                f"✅ {group}\n\n📅 *באיזה יום יהיה הטקס?*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(rows)
            )
        return

    # ── Belt wizard — day picker (for groups with 2 days) ──
    if action.startswith("bw_day|"):
        await query.answer()
        _, day, end_time = action.split("|")
        ss = sheets_sessions.get(user_id, {})
        h, m = map(int, end_time.split(":"))
        total = h * 60 + m - 10
        ceremony_time = f"{total//60:02d}:{total%60:02d}"
        ss["ceremony_day"] = day
        ss["ceremony_time"] = ceremony_time
        ss["step"] = "belt_wizard_link"
        sheets_sessions[user_id] = ss
        await query.message.reply_text(
            f"✅ יום {day} ב-{ceremony_time}\n\n📸 *קישור לסרטון המבחן?* (אופציונלי)",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("דלג ➡️", callback_data="bw_link|skip")],
                [InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")],
            ])
        )
        return

    # ── Belt wizard — video link (skip or typed) handled in text handler ──
    if action == "bw_link|skip":
        await query.answer()
        ss = sheets_sessions.get(user_id, {})
        ss["video_link"] = ""
        sheets_sessions[user_id] = ss
        await _belt_wizard_finish(query.message, user_id)
        return

    if action == "menu_belt_pay":
        await query.answer()
        await query.message.reply_text(
            "💳 לינק תשלום חגורה (60 ₪):\nhttps://private.invoice4u.co.il/Clearing/Invoice4UClearing.aspx?ProductId=4476&mobileApp=true",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="menu_belts")]]))
        return

    if action == "menu_belt_portal":
        await query.answer()
        await query.message.reply_text(
            "🌐 פורטל הכנה למבחני חגורה:\nhttps://wolvesjudotest.netlify.app/",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="menu_belts")]]))
        return

    if action == "cancel_flow":
        await query.answer("בוטל ✅")
        sheets_sessions.pop(user_id, None)
        calendar_sessions.pop(user_id, None)
        pending_plans.pop(user_id, None)
        await query.message.reply_text("בסדר, בוטל. במה אני יכול לעזור?")
        return

    if action == "quick_cal":
        await query.answer()
        calendar_sessions[user_id] = {"step": "wait_title"}
        await query.message.reply_text(
            "✏️ *מה הכותרת של האירוע?*",
            parse_mode="Markdown",
            reply_markup=cancel_button()
        )
        return

    if action.startswith("belt_add_cal"):
        await query.answer()
        parts = action.split("|")
        child_name    = parts[1] if len(parts) > 1 else ""
        belt_color    = parts[2] if len(parts) > 2 else ""
        ceremony_day  = parts[3] if len(parts) > 3 else ""
        ceremony_time = parts[4] if len(parts) > 4 else ""
        sheets_sessions[user_id] = {
            "step": "belt_cal_date",
            "child_name": child_name,
            "belt_color": belt_color,
            "ceremony_day": ceremony_day,
            "ceremony_time": ceremony_time,
        }
        time_hint = f" (שעה: {ceremony_time})" if ceremony_time else ""
        await query.message.reply_text(
            f"📅 מה התאריך המדויק של יום *{ceremony_day}*{time_hint}?\n(לדוגמה: `27/6` או `4/7`)",
            parse_mode="Markdown"
        )
        return

    if action == "menu_camp":
        await query.answer()
        stats = camp.get_stats()
        await query.message.reply_text(
            f"🏕 *מחנה קיץ — {stats['total']} ילדים רשומים*",
            parse_mode="Markdown", reply_markup=camp_menu_keyboard(),
        )
        return

    if action == "menu_lyla":
        await query.answer()
        stats = lyla.get_stats()
        await query.message.reply_text(
            f"🌸 *לילה יפני — {stats['total']} משתתפים*",
            parse_mode="Markdown", reply_markup=lyla_menu_keyboard(),
        )
        return

    if action == "menu_stats":
        await query.answer()
        await query.message.reply_text("📊 טוען נתונים...")
        # reuse stats logic inline
        lines = ["📊 *סטטיסטיקת וולבס ג׳ודו*\n"]
        try:
            c = camp.get_stats()
            lines.append(f"🏕 מחנה קיץ: *{c['total']}* ילדים")
            for b, n in c.get('by_branch', {}).items():
                lines.append(f"  • {b}: {n}")
        except Exception:
            pass
        try:
            ly = lyla.get_stats()
            lines.append(f"\n🌙 לילה יפני: *{ly['total']}* משתתפים")
            for b, n in ly.get('by_branch', {}).items():
                lines.append(f"  • {b}: {n}")
        except Exception:
            pass
        await query.message.reply_text("\n".join(lines), parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 תפריט ראשי", callback_data="menu_back")]]))
        return

    if action == "menu_cleanup":
        await query.answer()
        await query.edit_message_text("🧹 מנקה עמודות ריקות מכל הגיליונות...")
        try:
            results = att.cleanup_all_empty_columns()
            lines = ["✅ *ניקוי הושלם*\n"]
            total = sum(v for g in results.values() for v in g.values() if v > 0)
            for branch, groups in results.items():
                branch_total = sum(v for v in groups.values() if v > 0)
                if branch_total > 0:
                    lines.append(f"*{branch}*: {branch_total} עמודות")
                    for group, count in groups.items():
                        if count > 0:
                            lines.append(f"  • {group}: {count}")
            if total == 0:
                lines.append("לא נמצאו עמודות ריקות 👍")
            await query.edit_message_text("\n".join(lines), parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 תפריט ראשי", callback_data="menu_back")]]))
        except Exception as e:
            await query.edit_message_text(f"❌ שגיאה: {e}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="menu_back")]]))
        return

    if action == "menu_open_sheet":
        await query.answer()
        await query.message.reply_text(
            "📂 *בחר גיליון לפתיחה:*",
            parse_mode="Markdown",
            reply_markup=sheets_links_markup(),
        )
        return

    # ── תוכנית יום מלא ──
    if action == "menu_fullday":
        await query.answer()
        from datetime import date as _date
        today = _date.today()
        today_b = ws.today_branches()
        rows = []
        for b in tp.BRANCH_TABS:
            groups = ws.groups_for_branch_on_date(b, today)
            if groups:
                non_cancelled = [g for g in groups if not g.get('cancelled')]
                marker = f" ({len(non_cancelled)} קבוצות)" if non_cancelled else " 🚫"
            else:
                marker = ""
            rows.append([InlineKeyboardButton(f"{b}{marker}", callback_data=f"fd_branch|{b}")])
        rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
        await query.edit_message_text(
            f"📅 *תוכנית יום מלא*\n\nבחר סניף:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows)
        )
        return

    if action.startswith("fd_branch|"):
        await query.answer()
        branch = action.split("|", 1)[1]
        from datetime import date as _date
        dates = ws.next_training_dates(branch, n=5)
        today = _date.today()
        date_btns = []
        for d in dates:
            diff = (d - today).days
            prefix = "היום" if diff == 0 else "מחר" if diff == 1 else ws.day_name(d)
            groups = ws.groups_for_branch_on_date(branch, d)
            n_active = sum(1 for g in groups if not g.get('cancelled'))
            date_btns.append(InlineKeyboardButton(
                f"{prefix} {d.day}/{d.month} ({n_active} קב׳)",
                callback_data=f"fd_date|{branch}|{d.isoformat()}"
            ))
        rows = [date_btns[i:i+2] for i in range(0, len(date_btns), 2)]
        rows.append([InlineKeyboardButton("🔙 חזרה", callback_data="menu_fullday"),
                     InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
        await query.edit_message_text(
            f"📅 *יום מלא — {branch}*\n\nבחר תאריך:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows)
        )
        return

    if action.startswith("fd_date|"):
        await query.answer()
        _, branch, date_str = action.split("|", 2)
        from datetime import date as _date
        plan_date = _date.fromisoformat(date_str)
        groups = ws.groups_for_branch_on_date(branch, plan_date)
        day_he = ws.day_name(plan_date)
        active = [g for g in groups if not g.get('cancelled')]
        cancelled = [g for g in groups if g.get('cancelled')]
        lines = [f"🥋 *תוכנית יום מלא — {branch} | יום {day_he} {plan_date.day}/{plan_date.month}*\n"]
        lines.append("📝 *קבוצות לתוכנית:*")
        for g in active:
            lines.append(f"  ✅ {g['time']} — {g['name']}")
        for g in cancelled:
            lines.append(f"  🚫 {g['name']} — בוטל")
        lines.append("\n💬 *שלח לי את התוכנית ואשמור לכולם בבת אחת*")
        lines.append("_או כתוב: 'תכין תוכנית ליום מלא'_")
        sheets_sessions[user_id] = {
            "step": "fd_waiting_plan",
            "branch": branch,
            "plan_date": plan_date.isoformat(),
        }
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown",
            reply_markup=cancel_button())
        return

    # ── דוח נוכחות ──
    if action == "menu_absence_report":
        await query.answer()
        await query.message.chat.send_action("typing")
        try:
            log_data = load_json(Path("absence_log.json"), {})
            alerts = []
            warnings = []
            for name, records in log_data.items():
                recent = records[-5:]
                consec = sum(1 for r in reversed(recent) if r.get("absent"))
                total_abs = sum(1 for r in records if r.get("absent"))
                total = len(records)
                if consec >= 3:
                    last_branch = next((r.get("branch","") for r in reversed(records) if r.get("branch")), "")
                    parents = contacts_db.find_parent(name, last_branch or None)
                    phone = parents[0]["phone"] if parents else "—"
                    alerts.append(f"⚠️ *{name}* — {consec} ברצף | {phone}")
                elif consec >= 2 or (total > 0 and total_abs/total > 0.5):
                    warnings.append(f"🟡 {name} — {total_abs}/{total}")
            lines = ["📊 *דוח היעדרויות*\n"]
            if alerts:
                lines.append(f"🔴 *{len(alerts)} ספורטאים בסיכון גבוה:*")
                lines.extend(alerts[:10])
                lines.append("")
            if warnings:
                lines.append(f"🟡 *{len(warnings)} ספורטאים לתשומת לב:*")
                lines.extend(warnings[:8])
            if not alerts and not warnings:
                lines.append("✅ אין היעדרויות חריגות")
            await query.message.reply_text("\n".join(lines), parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 תפריט", callback_data="menu_back")]]))
        except Exception as e:
            await query.message.reply_text(f"❌ שגיאה: {e}")
        return

    # ── מי לא שילם ──
    if action == "menu_unpaid":
        await query.answer()
        from datetime import date as _date
        today = _date.today()
        months = []
        for m in range(9, 13):
            months.append(f"{m:02d}/{today.year - 1}")
        for m in range(1, today.month + 1):
            months.append(f"{m:02d}/{today.year}")
        recent_months = months[-4:]
        rows = [[InlineKeyboardButton(f"חודש {m}", callback_data=f"unpaid_month|{m}")] for m in reversed(recent_months)]
        rows.append([InlineKeyboardButton("🔙 חזרה", callback_data="menu_back")])
        await query.edit_message_text(
            "💰 *מי לא שילם — בחר חודש:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows)
        )
        return

    # ── הודעות להורים ──
    if action == "menu_parent_msgs":
        await query.answer()
        await query.edit_message_text(
            "📱 *הודעות להורים*\n\n"
            "בחר סוג הודעה:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚠️ הודעת היעדרות", callback_data="pm_absence")],
                [InlineKeyboardButton("💰 תזכורת תשלום",  callback_data="pm_payment")],
                [InlineKeyboardButton("📢 הודעה כללית",   callback_data="pm_general")],
                [InlineKeyboardButton("🔙 חזרה",           callback_data="menu_back")],
            ])
        )
        return

    if action == "pm_absence":
        await query.answer()
        sheets_sessions[user_id] = {"step": "pm_absence_name"}
        await query.message.reply_text(
            "📱 *הודעת היעדרות*\n\nמה שם הספורטאי?",
            parse_mode="Markdown",
            reply_markup=cancel_button()
        )
        return

    if action == "pm_payment":
        await query.answer()
        sheets_sessions[user_id] = {"step": "pm_payment_name"}
        await query.message.reply_text(
            "💰 *תזכורת תשלום*\n\nמה שם הספורטאי?",
            parse_mode="Markdown",
            reply_markup=cancel_button()
        )
        return

    if action == "pm_general":
        await query.answer()
        sheets_sessions[user_id] = {"step": "pm_general_branch"}
        rows = [[InlineKeyboardButton(b, callback_data=f"pm_gen_branch|{b}")] for b in contacts_db.CONTACT_FILES]
        rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
        await query.edit_message_text("📢 *הודעה כללית — לאיזה סניף?*",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
        return

    if action.startswith("pm_gen_branch|"):
        await query.answer()
        branch = action.split("|", 1)[1]
        contacts_list = contacts_db.get_branch_contacts(branch)
        if not contacts_list:
            await query.edit_message_text(f"❌ אין אנשי קשר עבור {branch}")
            return
        lines = [f"📢 *הודעה כללית — {branch}* ({len(contacts_list)} הורים)\n"]
        for c in contacts_list[:5]:
            lines.append(f"  📱 {c['raw']} — {c['phone']}")
        if len(contacts_list) > 5:
            lines.append(f"  ... ועוד {len(contacts_list)-5}")
        sheets_sessions.pop(user_id, None)
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="menu_back")]]))
        return

    # ── בדיקת מיילים ──
    if action == "menu_check_emails":
        await query.answer()
        await query.message.chat.send_action("typing")
        try:
            msgs = email_reader.fetch_new_emails()
            if msgs:
                await query.message.reply_text(
                    f"📧 *נמצאו {len(msgs)} מיילי תשלום*",
                    parse_mode="Markdown"
                )
            else:
                await query.message.reply_text("📧 אין מיילי תשלום חדשים")
        except Exception as e:
            await query.message.reply_text(f"❌ שגיאה: {e}")
        return

    # ── תחרויות ──
    if action == "menu_competitions":
        await query.answer()
        await query.message.chat.send_action("typing")
        try:
            s = comp_sheet.get_stats()
            lines = [f"🏆 *תחרויות — {s['total_competitions']} סה\"כ*\n"]
            for name, n in sorted(s['by_competition'].items(), key=lambda x: -x[1]):
                lines.append(f"  *{name}*: {n} ספורטאים")
            if s['medals']:
                lines.append("\n🥇 *מדליות:*")
                for k, v in s['medals'].items():
                    lines.append(f"  {k}: {v}")
            await query.message.reply_text("\n".join(lines), parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📊 פתח גיליון", url=SHEET_LINKS["תחרויות"])],
                    [InlineKeyboardButton("🔙 תפריט", callback_data="menu_back")],
                ]))
        except Exception as e:
            await query.message.reply_text(f"❌ שגיאה: {e}")
        return

    # ── ארכיון תוכניות ──
    if action == "menu_archive":
        await query.answer()
        try:
            import training_archive as _arc
            records = _arc._load()
            recent = records[-10:]
            lines = [f"📚 *ארכיון תוכניות — {len(records)} סה\"כ*\n"]
            for r in reversed(recent):
                date_str = r.get("saved_at", "")[:10]
                lines.append(f"  {date_str} | *{r['branch']}* {r['group']}")
            await query.message.reply_text("\n".join(lines), parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 תפריט", callback_data="menu_back")]]))
        except Exception as e:
            await query.message.reply_text(f"❌ שגיאה: {e}")
        return

    # ── אנשי קשר ──
    if action == "menu_contacts":
        await query.answer()
        try:
            cs = contacts_db.stats()
            lines = [f"📱 *אנשי קשר — {cs['total']} סה\"כ*\n"]
            for b, n in cs["by_branch"].items():
                lines.append(f"  {b}: {n} הורים")
            lines.append("\n💡 שלח `/contacts שם ספורטאי` לחיפוש הורה")
            await query.edit_message_text("\n".join(lines), parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📱 הכן הודעות להורים", callback_data="menu_parent_msgs")],
                    [InlineKeyboardButton("🔙 תפריט", callback_data="menu_back")],
                ]))
        except Exception as e:
            await query.message.reply_text(f"❌ שגיאה: {e}")
        return

    if action == "menu_help":
        await query.answer()
        help_text = (
            "💡 *מה אני יכול לעשות?*\n\n"
            "📅 *יומן* — מה יש לי היום/השבוע/החודש\n"
            "✅ *נוכחות* — סמן נוכחות לכל סניף\n"
            "🥋 *תוכנית יום מלא* — כל הקבוצות בסניף בבת אחת\n"
            "📱 *הודעות להורים* — היעדרות, תשלום, כללי\n"
            "💰 *תשלומים* — מי לא שילם + תזכורות\n"
            "🏆 *תחרויות* — נתוני תחרויות ומדליות\n"
            "💬 *כל שאלה חופשית* — שאל אותי כל מה שתרצה 😊"
        )
        await query.edit_message_text(help_text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="menu_back")]]))
        return
    # ─────────────────────────────

    # Undo dropout
    if action == "att_undo_dropout":
        await handle_undo_dropout(query, context)
        return

    # New student callbacks
    if action in ("new_student", "att_done"):
        await handle_new_student_callback(query, user_id, context)
        return

    # Calendar pick callback
    if action.startswith("cal_pick_"):
        calendar_name = action[len("cal_pick_"):]
        await handle_calendar_callback(query, user_id, calendar_name, context)
        return

    # Calendar delete callback
    if action.startswith("cal_del_"):
        suffix = action[len("cal_del_"):]
        if suffix == "cancel":
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer("בוטל")
            return
        try:
            idx = int(suffix)
            events = cal.get_recent_events(3)
            title = cal.delete_event(len(events) - 3 + idx if len(events) < 3 else idx)
            await query.edit_message_text(f"✅ המשימה *{title}* בוטלה מהיומן.", parse_mode="Markdown")
            await query.answer()
        except Exception as e:
            log.error("Calendar delete error: %s", e)
            await query.answer(f"שגיאה: {e}")
        return

    # Sheets (camp / lyla) callbacks
    if action == "save_direct":
        await query.answer()
        plan_data = pending_plans.get(user_id, {})
        original = plan_data.get("original", "") if isinstance(plan_data, dict) else str(plan_data)
        sheets_sessions[user_id] = {"step": "save_direct_date", "original": original}
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="📅 *לאיזה תאריך לשמור?*\n(לדוגמה: `היום`, `26/6`, `מחר`)",
            parse_mode="Markdown",
        )
        return

    if action.startswith("camp_") or action.startswith("lyla_"):
        await handle_sheets_callback(query, user_id, action, context)
        return

    # ── Invoice4u payment sync ──────────────────────────────────────────────────
    if action.startswith("inv4u_") or action == "inv4u_confirm_write":
        await handle_inv4u_callback(query, user_id, action, context)
        return

    # ── /edit — branch picker ──
    if action.startswith("edit_branch|"):
        await query.answer()
        branch = action.split("|", 1)[1]
        dates  = ws.next_training_dates(branch, 5)
        rows   = []
        for d in dates:
            day_he = ws.day_name(d)
            rows.append([InlineKeyboardButton(
                f"{day_he} {d.day}/{d.month}",
                callback_data=f"edit_date|{branch}|{d.isoformat()}"
            )])
        rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
        await query.edit_message_text(
            f"✏️ *{branch} — איזה תאריך?*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows)
        )
        return

    # ── /edit — date picked → load plan ──
    if action.startswith("edit_date|"):
        await query.answer()
        parts    = action.split("|")
        branch   = parts[1] if len(parts) > 1 else ""
        date_iso = parts[2] if len(parts) > 2 else ""
        if not branch or not date_iso:
            await query.answer("נתונים חסרים")
            return
        from datetime import date as _date_cls
        plan_date = _date_cls.fromisoformat(date_iso)
        day_he    = ws.day_name(plan_date)
        date_str  = f"{day_he} {plan_date.day}/{plan_date.month}"

        try:
            current = tp.load_plan_from_sheet(branch, plan_date)
        except Exception:
            current = None

        if current:
            lines = [f"📋 *תוכנית קיימת — {branch} | {date_str}*\n"]
            for g_name, items in current.items():
                lines.append(f"*{g_name}:*")
                for row_type, val in (items.items() if isinstance(items, dict) else []):
                    if val:
                        lines.append(f"  {row_type}: {val}")
            lines.append("\n✏️ שלח תוכנית מעודכנת לכתיבה:")
        else:
            lines = [f"📋 *{branch} | {date_str}* — אין תוכנית שמורה\n\nשלח תוכנית חדשה:"]

        sheets_sessions[user_id] = {
            "step": "fd_waiting_plan",
            "branch": branch,
            "plan_date": date_iso,
        }
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")]
            ])
        )
        return

    # Attendance callbacks handled separately (they call query.answer() themselves)
    if action.startswith("att_"):
        if action.startswith("att_start_"):
            _, branch_group = action.split("att_start_", 1)
            branch, group = branch_group.split("||")
            await query.answer()
            await query.edit_message_reply_markup(reply_markup=None)
            await context.bot.send_message(chat_id=query.message.chat_id, text="⏳ טוען רשימה...")
            await start_attendance_session(context.bot, query.message.chat_id, user_id, branch, group)
        else:
            await handle_attendance_callback(query, user_id, action, context)
        return

    await query.answer()

    if action == "approve":
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_chat_action(chat_id=query.message.chat_id, action="typing")
        try:
            reply = await call_claude(user_id, "אשרתי את התוכנית. אנא צור את קובץ ה-CSV המלא עכשיו.")
        except Exception as e:
            log.error("Claude API error: %s", e)
            await context.bot.send_message(chat_id=query.message.chat_id, text="❌ שגיאה. נסה שוב.")
            return

        if "```csv" in reply:
            csv_start = reply.index("```csv") + 6
            csv_end = reply.index("```", csv_start)
            csv_content = reply[csv_start:csv_end].strip()
            # Save CSV in pending_plans for later sheet upload
            if user_id not in pending_plans:
                pending_plans[user_id] = {}
            pending_plans[user_id]["csv"] = csv_content
            # Try to extract branch/group from history
            hist = get_history(user_id)
            for msg in reversed(hist):
                content = msg.get("content", "")
                for branch in ["סירקין", "חגור", "נווה ירק", "אהרונוביץ", "פונקציונלי", "נבחרת"]:
                    if branch in content:
                        pending_plans[user_id]["branch"] = branch
                        break
                if "branch" in pending_plans[user_id]:
                    break
            save_json(PENDING_FILE, pending_plans)
            await deliver_csv(context, query.message.chat_id, reply, csv_content)
        else:
            await context.bot.send_message(chat_id=query.message.chat_id, text=reply, reply_markup=approved_buttons())

    elif action == "edit":
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="✏️ מה תרצה לשנות? (שכבה, נושא, עצימות, הערה...)",
        )

    elif action == "alternative":
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_chat_action(chat_id=query.message.chat_id, action="typing")
        try:
            reply = await call_claude(user_id, "הצע תוכנית חלופית שונה לאותו האימון.")
        except Exception as e:
            log.error("Claude API error: %s", e)
            await context.bot.send_message(chat_id=query.message.chat_id, text="❌ שגיאה. נסה שוב.")
            return

        pending_plans[user_id] = reply
        save_json(PENDING_FILE, pending_plans)
        chunks = [reply[i:i+4096] for i in range(0, len(reply), 4096)]
        for i, chunk in enumerate(chunks):
            markup = plan_buttons() if i == len(chunks) - 1 else None
            await context.bot.send_message(chat_id=query.message.chat_id, text=chunk, reply_markup=markup)

    elif action == "new_plan":
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="👍 מעולה! שלח לי את הבקשה הבאה (סניף + יום + קבוצות).",
        )

    elif action == "save_to_sheet":
        await query.answer()
        # Ask which branch/group/date to save
        plan = pending_plans.get(user_id, {})
        branch = plan.get("branch", "")
        group = plan.get("group", "")
        if not branch or not group:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="📝 שלח לי: *סניף | קבוצה | תאריך* (לדוגמה: `סירקין | ז-בוגרים | 26/6`)",
                parse_mode="Markdown",
            )
            sheets_sessions[user_id] = {"step": "save_plan_meta", "csv": plan.get("csv", "")}
        else:
            sheets_sessions[user_id] = {
                "step": "save_plan_date", "branch": branch, "group": group,
                "csv": plan.get("csv", "")
            }
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"📅 לאיזה תאריך לשמור את התוכנית ל-*{branch} {group}*?\n(לדוגמה: `26/6` או `היום`)",
                parse_mode="Markdown",
            )


    # ── WhatsApp approval callbacks ───────────────────────────────
    if action.startswith("wa_send|"):
        await query.answer()
        key = action.split("|", 1)[1]
        pending = context.bot_data.get(key)
        if not pending:
            await query.edit_message_text("❌ ההודעה כבר לא קיימת")
            return
        if not wa_client.is_connected():
            await query.edit_message_text("❌ WhatsApp לא מחובר — שלח /wa\_connect")
            return
        ok = wa_client.send_message(pending["phone"], pending["message"])
        if ok:
            await query.edit_message_text(f"✅ נשלח בהצלחה ל-{pending['phone']}")
            context.bot_data.pop(key, None)
        else:
            await query.edit_message_text("❌ שגיאה בשליחה — בדוק /wa\_status")
        return

    if action.startswith("wa_cancel|"):
        await query.answer()
        key = action.split("|", 1)[1]
        context.bot_data.pop(key, None)
        await query.edit_message_text("❌ שליחה בוטלה")
        return

    if action.startswith("wa_star|"):
        group_id = action.split("|", 1)[1]
        groups_data = context.bot_data.get("wa_groups", {})
        group = groups_data.get(group_id, {})
        group_name = group.get("name", group_id)
        favs = _load_wa_favs()
        if group_id in favs:
            del favs[group_id]
            msg = f"💫 הוסר מהמועדפים: {group_name}"
        else:
            favs[group_id] = group_name
            msg = f"⭐ נשמר במועדפים: {group_name}"
        _save_wa_favs(favs)
        await query.answer(msg, show_alert=True)
        return

    if action.startswith("wa_group_pick|"):
        await query.answer()
        group_id = action.split("|", 1)[1]
        groups = context.bot_data.get("wa_groups", {})
        group = groups.get(group_id, {})
        group_name = group.get("name", group_id)
        # Store selected group, wait for user to type message
        context.bot_data["wa_pending_group"] = {"id": group_id, "name": group_name}
        await query.edit_message_text(
            f"✅ בחרת: *{group_name}*\n\nעכשיו שלח את ההודעה שתרצה לשלוח לקבוצה:",
            parse_mode="Markdown"
        )
        return



def attendance_student_keyboard(session: dict) -> InlineKeyboardMarkup:
    students = session["students"]
    absent_set = session.get("absent", set())
    dropout_set = session.get("dropouts", set())
    buttons = []
    for i, (row, name) in enumerate(students, start=1):
        if i in dropout_set:
            mark = "🖤"
        elif i in absent_set:
            mark = "🔴"
        else:
            mark = "🟢"
        buttons.append([
            InlineKeyboardButton(f"{mark} {name}", callback_data=f"att_toggle_{i}"),
            InlineKeyboardButton("🖤", callback_data=f"att_dropout_{i}"),
        ])
    buttons.append([
        InlineKeyboardButton("💾 שמור נוכחות", callback_data="att_save"),
        InlineKeyboardButton("❌ בטל", callback_data="att_cancel"),
    ])
    return InlineKeyboardMarkup(buttons)


def todays_schedule_keyboard(schedule: list) -> InlineKeyboardMarkup:
    """Keyboard with one button per group training today."""
    buttons = []
    for branch, group, time in schedule:
        buttons.append([InlineKeyboardButton(
            f"⏰ {time}  {branch} — {group}",
            callback_data=f"att_start_{branch}||{group}"
        )])
    return InlineKeyboardMarkup(buttons)


async def start_attendance_session(bot, chat_id: str, user_id: str, branch: str, group: str):
    """Load students and show attendance keyboard."""
    try:
        session = att.prepare_attendance(branch, group)
    except Exception as e:
        log.error("Attendance prepare error: %s", e)
        await bot.send_message(chat_id=chat_id, text=f"❌ שגיאה בטעינת הרשימה: {e}")
        return

    session["absent"] = set()
    attendance_sessions[user_id] = session

    keyboard = attendance_student_keyboard(session)
    await bot.send_message(
        chat_id=chat_id,
        text=f"📋 *{branch} — {group}* | {session['date']}\n\n"
             "לחץ על שם להחליף נוכחות 🟢/🔴\nבסוף לחץ *שמור*",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def handle_attendance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()

    if "נוכחות" not in text:
        return False

    branch, group = att.resolve_branch_group(text)

    # אם זיהה סניף וקבוצה ספציפיים — פתח ישר
    if branch and group:
        await update.message.reply_text("⏳ טוען רשימה...")
        await start_attendance_session(context.bot, update.effective_chat.id, user_id, branch, group)
        return True

    # אם כתב רק "נוכחות" (או שם סניף בלי קבוצה) — הצג את לוח היום
    schedule = att.get_todays_schedule()

    # סנן לפי סניף אם ציין
    for b in att.BRANCH_SHEETS:
        if b in text:
            schedule = [(br, gr, t) for br, gr, t in schedule if br == b]
            break

    if not schedule:
        day_names = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
        day = day_names[__import__("datetime").datetime.now(IL_TZ).weekday()]
        await update.message.reply_text(
            f"אין אימונים מתוכננים היום ({day}).\n"
            "ניתן לציין ידנית: *נוכחות סירקין ד-ו*",
            parse_mode="Markdown",
        )
        return True

    day_names = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
    day = day_names[__import__("datetime").datetime.now(IL_TZ).weekday()]
    await update.message.reply_text(
        f"📅 *אימוני היום — {day}*\nבחר קבוצה:",
        parse_mode="Markdown",
        reply_markup=todays_schedule_keyboard(schedule),
    )
    return True


async def handle_attendance_callback(query, user_id: str, action: str, context):
    """Handle att_toggle_N and att_save callbacks."""
    session = attendance_sessions.get(user_id)
    if not session:
        await query.answer("אין סשן נוכחות פעיל")
        return

    if action.startswith("att_toggle_"):
        idx = int(action.split("_")[-1])
        absent = session["absent"]
        if idx in absent:
            absent.discard(idx)
        else:
            absent.add(idx)
        await query.edit_message_reply_markup(reply_markup=attendance_student_keyboard(session))
        await query.answer()

    elif action.startswith("att_dropout_"):
        idx = int(action.split("_")[-1])
        students = session["students"]
        _, name = students[idx - 1]
        # Toggle dropout status
        dropout_set = session.setdefault("dropouts", set())
        if idx in dropout_set:
            dropout_set.discard(idx)
            await query.answer(f"↩️ {name} הוחזר לרשימה")
        else:
            dropout_set.add(idx)
            session["absent"].discard(idx)
            await query.answer(f"🖤 {name} סומן כפורש — יישמר בעת שמירה")
        await query.edit_message_reply_markup(reply_markup=attendance_student_keyboard(session))

    elif action == "att_cancel":
        branch = next((b for b, g in att.BRANCH_GROUPS.items() if session["sheet_name"] in g), "")
        try:
            att.cancel_attendance(session)
        except Exception as e:
            log.error("Attendance cancel error: %s", e)
            await query.edit_message_text(f"❌ שגיאה בביטול: {e}")
            return
        del attendance_sessions[user_id]
        msg = "↩️ נוכחות בוטלה" + (" — העמודה נמחקה" if session.get("col_is_new") else "")
        await query.edit_message_text(msg)
        await query.answer()
        # Refresh sheet design after cancel
        try:
            att.apply_sheet_design(branch, session["sheet_name"])
        except Exception:
            pass

    elif action == "att_save":
        # Show preview summary — do NOT write to sheets yet
        await query.answer()
        absent = session["absent"]
        dropout_indices = session.get("dropouts", set())
        students = session["students"]

        present_names = [name for i, (_, name) in enumerate(students, 1) if i not in absent and i not in dropout_indices]
        absent_names  = [name for i, (_, name) in enumerate(students, 1) if i in absent]
        dropout_names = [name for i, (_, name) in enumerate(students, 1) if i in dropout_indices]

        msg = f"📋 *סיכום — {session['sheet_name']} | {session['date']}*\n\n"
        msg += f"🟢 הגיעו ({len(present_names)}): {', '.join(present_names) or '—'}\n"
        if absent_names:
            msg += f"🔴 נעדרו ({len(absent_names)}): {', '.join(absent_names)}\n"
        if dropout_names:
            msg += f"🖤 פרשו: {', '.join(dropout_names)}\n"
        msg += "\nלחץ *אשר* כדי לשמור, או *ביטול* לחזור ולערוך."

        confirm_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ אשר ושמור", callback_data="att_confirm"),
            InlineKeyboardButton("↩️ חזור", callback_data="att_back"),
        ]])
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=confirm_markup)

    elif action == "att_back":
        # Return to the attendance keyboard for editing
        await query.answer()
        await query.edit_message_text(
            f"📋 *{session.get('sheet_name')} — {session.get('date')}*\n\nלחץ על שם להחליף נוכחות 🟢/🔴\nבסוף לחץ *שמור*",
            parse_mode="Markdown",
            reply_markup=attendance_student_keyboard(session),
        )

    elif action == "att_confirm":
        await query.answer("שומר...")
        absent = session["absent"]
        dropout_indices = session.get("dropouts", set())
        students = session["students"]

        # Clear undo file before processing this session's dropouts
        att.clear_dropout_undo()

        # Process dropouts first (reverse order so row indices stay valid)
        dropout_names = []
        for idx in sorted(dropout_indices, reverse=True):
            try:
                name = att.mark_as_dropout(session, idx)
                dropout_names.append(name)
                abt.remove_student(name)
            except Exception as e:
                log.error("Dropout error: %s", e)

        # Mark attendance for remaining students
        try:
            att.mark_attendance(session, absent)
        except Exception as e:
            log.error("Attendance mark error: %s", e)
            await query.edit_message_text(f"❌ שגיאה בסימון: {e}")
            return

        present_names = [name for i, (_, name) in enumerate(students, 1) if i not in absent and i not in dropout_indices]
        absent_names  = [name for i, (_, name) in enumerate(students, 1) if i in absent]

        # Record absences and check for 3-streak alerts
        branch = next((b for b, g_list in att.BRANCH_GROUPS.items() if session["sheet_name"] in g_list), "")
        alert_names = abt.record_attendance(students, absent, session["date"], branch, session["sheet_name"])

        del attendance_sessions[user_id]

        msg = f"✅ *{session['sheet_name']} — {session['date']}*\n"
        msg += f"🟢 הגיעו ({len(present_names)}): {', '.join(present_names)}\n"
        if absent_names:
            msg += f"🔴 נעדרו ({len(absent_names)}): {', '.join(absent_names)}\n"
        if dropout_names:
            msg += f"🖤 פרשו: {', '.join(dropout_names)}\n\n"
            msg += "⚠️ *זכור לבטל תשלום עבור:*\n"
            msg += "\n".join(f"• {n}" for n in dropout_names)

        # Store session for potential new student addition
        new_student_sessions[user_id] = {"session": session, "step": None}

        # Build reply markup — add undo button if there were dropouts
        if dropout_names:
            undo_btn = InlineKeyboardButton("↩️ בטל פרישה", callback_data="att_undo_dropout")
            done_markup = InlineKeyboardMarkup([
                [undo_btn],
                *attendance_done_buttons().inline_keyboard,
            ])
        else:
            done_markup = attendance_done_buttons()

        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=done_markup)

        # Calendar reminders for 3-streak absences + dropouts
        cal_msgs = []
        for name in alert_names:
            abt.create_calendar_reminder(name, branch, session["sheet_name"], session["date"])
            cal_msgs.append(f"📞 {name} — 3 היעדרויות ברצף")
        for name in dropout_names:
            event_id, _ = abt.create_dropout_reminder(name, branch, session["sheet_name"], session["date"])
            if event_id:
                att.save_dropout_calendar_event(name, event_id)
            cal_msgs.append(f"🖤 {name} — לבטל רישום")
        if cal_msgs:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="📅 *נוצרו תזכורות ביומן למחר ב-09:00:*\n" + "\n".join(f"• {m}" for m in cal_msgs),
                parse_mode="Markdown"
            )

        # Parent contact lookup for absent students
        if absent_names and branch:
            try:
                contact_lines = ["📱 *פרטי הורים לנעדרים:*\n"]
                found_any = False
                for name in absent_names[:8]:  # limit to 8
                    parents = contacts_db.find_parent(name, branch)
                    if parents:
                        p = parents[0]
                        # Check for consecutive absences
                        log_data = load_json(Path("absence_log.json"), {})
                        records = log_data.get(name, [])
                        consec = sum(1 for r in reversed(records[-5:]) if r.get("absent"))
                        streak_note = f" ⚠️ {consec} ברצף" if consec >= 2 else ""
                        contact_lines.append(f"*{name}*{streak_note}")
                        contact_lines.append(f"  📞 {p['parent_name']}: `{p['phone']}`")
                        found_any = True
                    else:
                        contact_lines.append(f"*{name}* — לא נמצא איש קשר")
                if found_any:
                    # Store absent names for messaging flow
                    sheets_sessions[user_id] = {
                        "step": "absent_msg_ready",
                        "absent_names": absent_names,
                        "branch": branch,
                        "date": session["date"],
                    }
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text="\n".join(contact_lines),
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("📩 הכן הודעות לכולם", callback_data="absent_msgs_all"),
                        ]])
                    )
            except Exception as e:
                log.error("Contact lookup error: %s", e)

        # Update sheet design (deletes empty columns, re-styles)
        try:
            att.apply_sheet_design(session["branch"], session["sheet_name"])
        except Exception:
            pass  # design update is non-critical


async def handle_undo_dropout(query, context):
    """Restore all dropouts from the last attendance save."""
    await query.answer("מחזיר...")
    try:
        restored = att.undo_dropouts()
        if restored:
            names = ", ".join(restored)
            # Re-add students to absence log (they're back, start fresh)
            for name in restored:
                pass  # absence history was already cleared on dropout — they start clean
            await query.edit_message_text(
                f"↩️ *בוטלה פרישה:* {names}\n\nהספורטאים הוחזרו לרשימה הפעילה.",
                parse_mode="Markdown",
            )
        else:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer("הפעולה כבר בוצעה או שהבוט הופעל מחדש — אין אפשרות לבטל.", show_alert=True)
    except Exception as e:
        log.error("Undo dropout error: %s", e)
        await query.answer(f"שגיאה: {e}", show_alert=True)


async def handle_new_student_callback(query, user_id: str, context):
    """Handle new_student and att_done callbacks."""
    if query.data == "att_done":
        new_student_sessions.pop(user_id, None)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.answer("✅ סיום")
        return

    if query.data == "new_student":
        ns = new_student_sessions.get(user_id)
        if not ns:
            await query.answer("אין סשן פעיל")
            return
        ns["step"] = "first_name"
        await query.answer()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="➕ *שם פרטי של המצטרף:*",
            parse_mode="Markdown",
        )


async def handle_new_student_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text input during new student flow. Returns True if consumed."""
    user_id = str(update.effective_user.id)
    ns = new_student_sessions.get(user_id)
    if not ns or not ns.get("step"):
        return False

    text = update.message.text.strip()

    if ns["step"] == "first_name":
        ns["first_name"] = text
        ns["step"] = "last_name"
        await update.message.reply_text("📝 *שם משפחה:*", parse_mode="Markdown")
        return True

    if ns["step"] == "last_name":
        first = ns["first_name"]
        last = text
        session = ns["session"]
        try:
            new_row, full_name = att.add_new_student(session, first, last)
        except Exception as e:
            log.error("Add student error: %s", e)
            await update.message.reply_text(f"❌ שגיאה: {e}")
            ns["step"] = None
            return True

        ns["step"] = None

        # Calendar reminder for tomorrow to follow up with new student's parents
        try:
            today = session.get("date", datetime.now(IL_TZ).strftime("%d/%m/%Y"))
            branch = next((b for b, g_list in att.BRANCH_GROUPS.items() if session["sheet_name"] in g_list), "")
            abt.create_new_student_reminder(full_name, branch, session["sheet_name"], today)
        except Exception as e:
            log.error("New student reminder error: %s", e)

        await update.message.reply_text(
            f"✅ *{full_name}* נוסף לרשימה וסומן כהגיע 🟢\n📅 נוצרה תזכורת ביומן למחר לדבר עם ההורים\n\nיש עוד מצטרף?",
            parse_mode="Markdown",
            reply_markup=new_student_again_buttons(),
        )
        return True

    return False


CAL_TRIGGERS = ("יומן", "תזכיר", "תזכורת", "משימה", "אירוע")
CAL_DELETE_TRIGGERS = ("בטל משימה", "מחק משימה", "בטל אירוע", "מחק אירוע", "בטל תזכורת")
CAL_QUERY_TRIGGERS = (
    "מה יש לי", "מה עומד לי", "מה אני צריך", "מה יש היום", "מה יש מחר",
    "מה יש השבוע", "מה יש החודש", "לוח זמנים", "סדר יום", "מה קורה",
    "מה יש ב", "מה יש מ", "תראה לי את היומן", "היומן שלי",
)


async def handle_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle calendar task creation flow. Returns True if consumed."""
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()

    # Active session — continue collecting info
    cs = calendar_sessions.get(user_id)
    if cs:
        step = cs.get("step")

        if step == "wait_title":
            cs["title"] = text
            cs["step"] = "wait_date"
            await update.message.reply_text(
                "📅 *מתי?*\nלדוגמה: `מחר`, `מחר ב10:00`, `25/6`, `ביום שישי ב18:00`",
                parse_mode="Markdown",
                reply_markup=cancel_button()
            )
            return True

        if step == "wait_date":
            event_date, time_str = cal.parse_date_hebrew(text)
            if not event_date:
                await update.message.reply_text(
                    "❌ לא הבנתי את התאריך. נסה שוב:\n`מחר`, `25/6`, `ביום שישי`, `מחר ב10:00`",
                    parse_mode="Markdown"
                )
                return True
            cs["date"] = event_date
            cs["time"] = time_str

            # Auto-detect calendar from title
            title = cs.get("title", "").lower()
            auto_cal = None
            if any(k in title for k in ["חגורה", "טקס", "מעבר חגורה"]):
                auto_cal = "טקסי מעבר חגורה"
            elif any(k in title for k in ["נבחרת", "אימון", "אמון", "ג'ודו", "ג׳ודו", "מועדון"]):
                auto_cal = "אימוני מועדון הג'ודו"
            elif any(k in title for k in ["פגישה", "פגישות"]):
                auto_cal = "פגישות"
            elif any(k in title for k in ["תזכורת", "תזכיר"]):
                auto_cal = "תזכורות"

            if auto_cal:
                cs["calendar"] = auto_cal
                await _create_calendar_event(update.effective_chat.id, user_id, cs, context.bot)
            else:
                cs["step"] = "wait_calendar"
                await update.message.reply_text(
                    "📂 *באיזה יומן לשמור?*",
                    parse_mode="Markdown",
                    reply_markup=with_cancel(calendar_buttons()),
                )
            return True

        return False

    # Calendar query — "מה יש לי היום/השבוע/..."
    if any(t in text for t in CAL_QUERY_TRIGGERS):
        await _calendar_query(update, context, text)
        return True

    # Delete request
    if any(t in text for t in CAL_DELETE_TRIGGERS):
        events = cal.get_recent_events(3)
        if not events:
            await update.message.reply_text("אין משימות אחרונות לביטול.")
            return True
        lines = ["🗑 *איזו משימה לבטל?*\n"]
        for i, e in enumerate(events, 1):
            emoji = cal.CALENDAR_EMOJI.get(e["calendar_name"], "📅")
            time_str = f" ב-{e['time']}" if e.get("time") else ""
            lines.append(f"{i}. {emoji} *{e['title']}* | {e['date']}{time_str}")
        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=recent_events_buttons(events),
        )
        return True

    # New task trigger
    if not any(t in text for t in CAL_TRIGGERS):
        return False

    # Always ask step by step — start with title
    calendar_sessions[user_id] = {"step": "wait_title"}
    await update.message.reply_text("✏️ *מה הכותרת של המשימה?*", parse_mode="Markdown", reply_markup=cancel_button())
    return True


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process photos sent to the bot — forward to Claude vision."""
    import base64
    user_id = str(update.effective_user.id)
    photo = update.message.photo[-1]  # largest size
    caption = update.message.caption or ""

    await update.message.reply_text("📸 מעבד את התמונה...")
    file = await context.bot.get_file(photo.file_id)
    data = await file.download_as_bytearray()
    image_b64 = base64.b64encode(bytes(data)).decode()

    reply = await call_claude(user_id, caption, image_b64=image_b64)
    try:
        conversation_log.log_conversation(f"[תמונה] {caption}", reply, action="תמונה")
    except Exception:
        pass
    await update.message.reply_text(reply)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle XLS/XLSX file uploads — detect invoice4u report and start payment sync flow."""
    doc = update.message.document
    if not doc:
        return
    fname = doc.file_name or ""
    if not (fname.endswith(".xls") or fname.endswith(".xlsx")):
        return  # not a spreadsheet — ignore

    user_id = str(update.effective_user.id)
    await update.message.reply_text("📂 מוריד ומנתח קובץ...")

    try:
        file = await context.bot.get_file(doc.file_id)
        data = bytes(await file.download_as_bytearray())
        records = invoice4u_reader.read_xls(data)
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה בקריאת הקובץ: {e}")
        return

    if not records:
        await update.message.reply_text("⚠️ לא נמצאו רשומות בקובץ")
        return

    months = invoice4u_reader.available_months(records)

    if len(months) == 1:
        await _inv4u_start_month(update, context, user_id, records, months[0])
    else:
        # Multiple months — let user pick
        rows = [
            [InlineKeyboardButton(m, callback_data=f"inv4u_month|{m}")]
            for m in months[-6:]  # last 6 months
        ]
        rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
        payment_sync_sessions[user_id] = {"records": records, "step": "pick_month"}
        await update.message.reply_text(
            f"📊 *קובץ invoice4u* — {len(records)} רשומות\n\nאיזה חודש לעבד?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows)
        )


async def _inv4u_start_month(update, context, user_id: str, records: list, month_key: str):
    """Start processing invoice4u records for a specific month ('Month YYYY')."""
    parts     = month_key.rsplit(" ", 1)
    month_he  = parts[0]
    year      = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 2026
    month_recs = invoice4u_reader.filter_month(records, month_he, year)
    summ       = invoice4u_reader.summarise(month_recs)

    # update may be a telegram.Update or a CallbackQuery — both have .message
    loading_msg = await update.message.reply_text("⏳ טוען תלמידים...")

    try:
        sheet_students = invoice4u_sync.load_all_students()
    except Exception as e:
        await loading_msg.edit_text(f"❌ שגיאה בטעינת תלמידים: {e}")
        return

    mapping  = payment_matcher.load_mapping()
    monthly  = summ["monthly"] + summ["monthly_unusual"]
    belts    = summ["belt"]

    matched_monthly = payment_matcher.match_records(monthly, sheet_students, mapping)
    matched_belts   = payment_matcher.match_records(belts,   sheet_students, mapping)

    auto_cnt    = sum(1 for m in matched_monthly if m["status"] in ("saved", "auto"))
    unknown_cnt = sum(1 for m in matched_monthly if m["status"] == "unknown")
    belt_cnt    = len(belts)
    skip_cnt    = len(summ["club_transfer"]) + len(summ["other"])

    payment_sync_sessions[user_id] = {
        "step":            "review",
        "month":           month_he,
        "year":            year,
        "matched_monthly": matched_monthly,
        "matched_belts":   matched_belts,
        "unknowns":        [m for m in matched_monthly if m["status"] == "unknown"],
        "current_unknown": 0,
        "sheet_students":  sheet_students,
        "mapping":         mapping,
    }

    lines = [
        f"📊 *invoice4u — {month_he} {year}*\n",
        f"• תשלומים חודשיים: {len(monthly)}",
        f"  ✅ {auto_cnt} זוהו אוטומטית",
    ]
    if unknown_cnt:
        lines.append(f"  ❓ {unknown_cnt} לא זוהו")
    if belt_cnt:
        lines.append(f"• 🥋 {belt_cnt} חגורות")
    if skip_cnt:
        lines.append(f"• ⛔ {skip_cnt} מדולגים (העברות/אחר)")

    rows = [[InlineKeyboardButton("▶️ בדיקה — התחל", callback_data="inv4u_start_review")]]
    rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])

    await loading_msg.edit_text("\n".join(lines), parse_mode="Markdown",
                                reply_markup=InlineKeyboardMarkup(rows))


def _inv4u_unknown_prompt(ss: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Build prompt for current unknown record."""
    unknowns = ss.get("unknowns", [])
    idx      = ss.get("current_unknown", 0)
    total    = len(unknowns)

    if idx >= total:
        return "", InlineKeyboardMarkup([])

    item = unknowns[idx]
    rec  = item["record"]
    cname = rec["customer_name"]
    amount = rec["amount"]
    date_s = rec["date"]
    children = rec.get("children", [])
    child_str = " / ".join(children) if children else ""

    text = (
        f"❓ *תשלום לא ידוע* ({idx + 1}/{total})\n\n"
        f"שם בחשבונית: *{cname}*"
    )
    if child_str:
        text += f"\nילד/ים: *{child_str}*"
    text += f"\nסכום: *{amount}₪* | תאריך: {date_s}\n\n"
    text += "מה שם הספורטאי/ת? (שלח שם לחיפוש)"

    buttons = [[InlineKeyboardButton("⏭ דלג", callback_data="inv4u_unknown_skip")]]
    return text, InlineKeyboardMarkup(buttons)


async def _inv4u_show_summary(update_or_query, user_id: str):
    """Show the final confirmation summary before writing to sheets."""
    ss = payment_sync_sessions.get(user_id, {})
    matched_monthly = ss.get("matched_monthly", [])
    matched_belts   = ss.get("matched_belts", [])
    month_he        = ss.get("month", "")
    year            = ss.get("year", "")

    to_write = [m for m in matched_monthly if m["status"] in ("saved", "auto", "confirmed")]
    skipped  = [m for m in matched_monthly if m["status"] in ("unknown", "skipped")]

    lines = [f"✅ *סיכום לאישור — {month_he} {year}*\n"]
    lines.append(f"📋 *{len(to_write)} תשלומים לכתיבה:*")
    for m in to_write[:15]:
        s = m["student"]
        name = f"{s['first']} {s['last']}" if s else "?"
        branch = s.get("branch", "") if s else ""
        lines.append(f"  • {name} ({branch}) — {m['record']['amount']}₪")
    if len(to_write) > 15:
        lines.append(f"  … ועוד {len(to_write) - 15}")

    if matched_belts:
        lines.append(f"\n🥋 *{len(matched_belts)} חגורות:*")
        for m in matched_belts:
            s = m.get("student")
            rec = m["record"]
            if s:
                lines.append(f"  • {s['first']} {s['last']} ({s.get('branch','')}) — {rec['date']}")
            else:
                lines.append(f"  • {rec['customer_name']} — {rec['date']}")

    if skipped:
        lines.append(f"\n❓ *{len(skipped)} לא זוהו (ידולגו):*")
        for m in skipped[:5]:
            lines.append(f"  • {m['record']['customer_name']} — {m['record']['amount']}₪")
        if len(skipped) > 5:
            lines.append(f"  … ועוד {len(skipped) - 5}")

    ss["to_write"] = to_write
    payment_sync_sessions[user_id] = ss

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ כתוב הכל לגיליון", callback_data="inv4u_confirm_write")],
        [InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")],
    ])

    msg = "\n".join(lines)
    if hasattr(update_or_query, "message"):
        await update_or_query.message.reply_text(msg, parse_mode="Markdown", reply_markup=markup)
    else:
        await update_or_query.edit_message_text(msg, parse_mode="Markdown", reply_markup=markup)


async def _plan_wizard_extract(branch: str, group: str, plan_text: str) -> list[str]:
    """Use Claude to extract plan items in the correct format for the sheet."""
    import anthropic as _anthropic
    _client = _anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Determine row labels by branch/group
    if branch == "פונקציונלי":
        row_labels = ["חימום", "תרגול א", "תרגול ב", "תרגול ג", "תרגול ד", "כוח", "הערות", "סיום"]
    elif group in ("נבחרת", "ז-בוגרים", "ז-ח", 'ט-י"ב'):
        row_labels = ["חימום", "טכניקה", "תרגול", "קרבות א", "קרבות ב", "כוח", "הערות", "סיום"]
    else:
        row_labels = ["חימום", "תרגול א", "תרגול ב", "קרבות", "כוח", "הערות", "סיום", ""]

    labels_str = " | ".join(f"{i+1}. {l}" for i, l in enumerate(row_labels))
    prompt = f"""אתה עוזר לטופז מאמן ג'ודו. עליך לפרק תוכנית אימון לשורות הגיליון.

הקבוצה: {branch} — {group}
שורות הגיליון (בדיוק 8): {labels_str}

תוכנית האימון:
{plan_text}

החזר בדיוק 8 שורות, אחת לכל תא. אם אין תוכן לשורה — כתוב רק מקף (-).
ללא מספור, ללא כותרות — רק הטקסט לכל שורה.
שמור על הסגנון של טופז: קצר, טכני, מדויק."""

    resp = _client.messages.create(
        model="claude-sonnet-4-6", max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )
    items = [l.strip() for l in resp.content[0].text.strip().splitlines() if l.strip()]
    # Pad/trim to 8
    while len(items) < 8:
        items.append("")
    return items[:8]


async def _plan_wizard_preview(message, user_id: str, ss: dict):
    """Show parsed plan preview with confirm/edit buttons."""
    branch = ss.get("branch", "")
    group  = ss.get("group", "")
    plan_text = ss.get("plan_text", "")
    plan_date_str = ss.get("plan_date", "")

    try:
        items = await _plan_wizard_extract(branch, group, plan_text)
    except Exception as e:
        await message.reply_text(f"❌ שגיאה בפירוק התוכנית: {e}")
        return

    ss["parsed_items"] = items
    sheets_sessions[user_id] = ss

    ROW_LABELS = ["חימום", "תרגול א", "תרגול ב", "שורה 4", "שורה 5", "כוח", "הערות", "סיום"]
    preview = f"📋 *{branch} — {group}* | {plan_date_str}\n\n"
    for i, (label, item) in enumerate(zip(ROW_LABELS, items)):
        if item and item != "-":
            preview += f"*{label}:* {item}\n"

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ אשר ושמור", callback_data="pw_confirm")],
        [InlineKeyboardButton("✏️ שלח תוכנית מחדש", callback_data="pw_reedit")],
        [InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")],
    ])
    await message.reply_text(preview, parse_mode="Markdown", reply_markup=markup)


async def _plan_wizard_save(message, user_id: str, ss: dict):
    """Save parsed items to the training plans sheet."""
    from datetime import date as date_cls
    branch    = ss.get("branch", "")
    group     = ss.get("group", "")
    items     = ss.get("parsed_items", [])
    date_str  = ss.get("plan_date", "")

    try:
        plan_date = date_cls.fromisoformat(date_str)
    except Exception:
        plan_date = date_cls.today()

    try:
        result = tp.save_plan_to_sheet(branch, group, plan_date, items)
        await message.reply_text(
            f"✅ *נשמר בגיליון!*\n\n"
            f"📊 {branch} — {group}\n"
            f"📅 {plan_date.strftime('%d/%m/%Y')}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 תפריט", callback_data="menu_main")]]),
        )
    except Exception as e:
        await message.reply_text(f"❌ שגיאה בשמירה: {e}")


# ── Gmail helpers ─────────────────────────────────────────────────────────────

def gmail_send(to: str, subject: str, body: str, attachments: list[str] | None = None):
    """Send email via Gmail SMTP using App Password."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    if not GMAIL_APP_PASS:
        raise ValueError("GMAIL_APP_PASS לא מוגדר")

    msg = MIMEMultipart()
    msg["From"]    = GMAIL_USER
    msg["To"]      = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    for path in (attachments or []):
        with open(path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{Path(path).name}"')
        msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
        srv.login(GMAIL_USER, GMAIL_APP_PASS)
        srv.send_message(msg)


def gmail_fetch_unread(max_results: int = 10) -> list[dict]:
    """Fetch unread emails via IMAP."""
    import imaplib, email as email_lib
    from email.header import decode_header

    if not GMAIL_APP_PASS:
        return []

    results = []
    with imaplib.IMAP4_SSL("imap.gmail.com") as M:
        M.login(GMAIL_USER, GMAIL_APP_PASS)
        M.select("INBOX")
        _, ids = M.search(None, "UNSEEN")
        uid_list = ids[0].split()[-max_results:]
        for uid in reversed(uid_list):
            _, data = M.fetch(uid, "(RFC822)")
            msg = email_lib.message_from_bytes(data[0][1])
            subj_raw, enc = decode_header(msg["Subject"] or "")[0]
            subj = subj_raw.decode(enc or "utf-8") if isinstance(subj_raw, bytes) else subj_raw
            sender = msg.get("From", "")
            body = ""
            attachments = []
            for part in msg.walk():
                ct = part.get_content_type()
                disp = str(part.get("Content-Disposition", ""))
                if ct == "text/plain" and "attachment" not in disp:
                    body += part.get_payload(decode=True).decode("utf-8", errors="ignore")
                elif "attachment" in disp:
                    fname = part.get_filename()
                    if fname:
                        attachments.append(fname)
            results.append({"subject": subj, "from": sender, "body": body[:500], "attachments": attachments})
    return results


async def cmd_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/email — שלח/קרא מיילים דרך הבוט."""
    args = context.args
    if not args:
        unread = gmail_fetch_unread(5)
        if not unread:
            await update.message.reply_text("📭 אין מיילים חדשים ב-topazjudo@gmail.com")
            return
        lines = [f"📬 *{len(unread)} מיילים חדשים:*"]
        for m in unread:
            att = f" 📎 {', '.join(m['attachments'])}" if m['attachments'] else ""
            lines.append(f"\n*{m['subject']}*\nמ: {m['from']}{att}\n{m['body'][:120]}...")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    # /email <to> <subject> | <body>
    text = " ".join(args)
    if "|" in text:
        header, body = text.split("|", 1)
        parts = header.strip().split(None, 1)
        to = parts[0] if parts else GMAIL_USER
        subject = parts[1] if len(parts) > 1 else "הודעה מהבוט"
    else:
        to, subject, body = GMAIL_USER, "הודעה מהבוט", text

    try:
        gmail_send(to, subject.strip(), body.strip())
        await update.message.reply_text(f"✅ מייל נשלח ל-{to}")
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {e}")


async def cmd_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/payments — הפעל בדיקת מיילים עכשיו."""
    chat_id = update.effective_chat.id
    await update.message.reply_text("🔍 בודק מיילים חדשים...")
    await update.message.chat.send_action("typing")
    try:
        emails = email_reader.fetch_new_emails()
        if not emails:
            await update.message.reply_text("✅ אין מיילים חדשים לבדיקה.")
            return
        await update.message.reply_text(f"📬 נמצאו {len(emails)} מיילים — מנתח...")
        for em in emails:
            prompt = (
                f"מייל שהתקבל:\nנושא: {em['subject']}\nמ: {em['sender']}\nתוכן:\n{em['body'][:1500]}\n\n"
                "האם זה מייל שקשור לתשלום של ספורטאי ג'ודו? "
                "אם כן, חלץ: שם הספורטאי, חודש התשלום, סכום. "
                "השב בפורמט JSON בלבד:\n"
                '{\"is_payment\": true, \"student_name\": \"שם\", \"month\": \"ספטמבר\", \"amount\": \"200\"}\n'
                "אם לא קשור: {\"is_payment\": false}"
            )
            try:
                import re as _re
                resp = client.messages.create(
                    model="claude-haiku-4-5-20251001", max_tokens=200,
                    messages=[{"role": "user", "content": prompt}]
                )
                json_match = _re.search(r'\{.*\}', resp.content[0].text.strip(), _re.DOTALL)
                if not json_match:
                    email_reader.mark_skipped(em["id"]); continue
                data = json.loads(json_match.group())
            except Exception:
                email_reader.mark_skipped(em["id"]); continue

            if not data.get("is_payment"):
                email_reader.mark_skipped(em["id"]); continue

            student_name = data.get("student_name", "")
            month        = data.get("month", "")
            amount       = data.get("amount", "")
            student      = payments_sheet.find_student(student_name) if student_name else None

            key = f"pay_{em['id']}"
            pending_payments[key] = {
                "email_id": em["id"], "subject": em["subject"], "sender": em["sender"],
                "student_name": student_name, "student": student, "month": month, "amount": amount,
            }
            if student:
                current   = payments_sheet.get_month_value(student["row"], month)
                paid_info = payments_sheet.payment_summary_row(student)
                student_line = f"✅ נמצא: *{student['full_name']}* ({student['club']})"
                current_line = f"ערך נוכחי ב{month}: {current or 'ריק'}\n{paid_info}"
            else:
                student_line = f"⚠️ לא נמצא ספורטאי: *{student_name}*"
                current_line = ""
            msg = (
                f"💰 *מייל תשלום חדש*\n{student_line}\n"
                f"חודש: {month} | סכום: {amount}₪\n"
                f"מ: {em['sender']}\n{current_line}"
            )
            await context.bot.send_message(
                chat_id=chat_id, text=msg, parse_mode="Markdown",
                reply_markup=payment_approval_buttons(key)
            )
            email_reader.mark_seen(em["id"])
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {e}")


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/myid — הצג את ה-chat ID שלך."""
    cid = update.effective_chat.id
    uid = update.effective_user.id
    await update.message.reply_text(f"Chat ID: `{cid}`\nUser ID: `{uid}`", parse_mode="Markdown")


async def cmd_unpaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/unpaid [חודש] — מי לא שילם."""
    await update.message.chat.send_action("typing")
    args = context.args
    month = " ".join(args) if args else None

    if month and month not in payments_report.MONTHS:
        months_str = ", ".join(payments_report.MONTHS)
        await update.message.reply_text(f"❌ חודש לא מוכר. אפשרויות: {months_str}")
        return

    if month:
        msg = payments_report.format_unpaid_message(month)
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("📱 הכן הודעת ווטסאפ", callback_data=f"unpaid_wa|{month}"),
        ]])
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=markup)
    else:
        # Show month picker
        rows = []
        for i in range(0, len(payments_report.MONTHS), 3):
            rows.append([
                InlineKeyboardButton(m, callback_data=f"unpaid_month|{m}")
                for m in payments_report.MONTHS[i:i+3]
            ])
        await update.message.reply_text(
            "💰 *מי לא שילם?*\n\nבחר חודש:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows)
        )




async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/report — דו״ח חודשי מנהלי."""
    await update.message.chat.send_action("typing")
    try:
        msg = payments_report.format_monthly_report()
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {e}")


async def cmd_dropout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/dropout — ספורטאים שפספסו 3+ אימונים ברצף."""
    await update.message.reply_text("⏳ סורק גיליונות נוכחות... זה עלול לקחת כ-30 שניות.")
    await update.message.chat.send_action("typing")
    try:
        at_risk = dropout_detector.find_at_risk_students(consecutive=3)
        msg = dropout_detector.format_at_risk_message(at_risk)
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {e}")


async def contacts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/contacts [שם] — חיפוש הורה לפי שם ספורטאי."""
    args = context.args
    if args:
        name = " ".join(args)
        results = contacts_db.find_parent(name)
        if not results:
            await update.message.reply_text(f"❌ לא נמצא הורה עבור: {name}")
            return
        lines = [f"🔍 *תוצאות עבור {name}:*\n"]
        for r in results[:5]:
            lines.append(f"*{r['parent_name']}* — `{r['phone']}`")
            if len(r["phones"]) > 1:
                lines.append(f"  טלפון נוסף: `{r['phones'][1]}`")
            lines.append(f"  סניף: {r['branch']}")
            lines.append("")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    else:
        # Summary stats
        cs = contacts_db.stats()
        lines = [f"📱 *אנשי קשר — {cs['total']} סה\"כ*\n"]
        for b, n in cs["by_branch"].items():
            lines.append(f"  {b}: {n}")
        lines.append("\n*שימוש:* `/contacts שם ספורטאי`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def monthly_report_job(context):
    """Background job — sends monthly financial report on the 1st of each month."""
    if not TOPAZ_CHAT_ID:
        return
    today = datetime.now(IL_TZ)
    if today.day != 1:
        return
    try:
        msg = payments_report.format_monthly_report()
        await context.bot.send_message(
            chat_id=TOPAZ_CHAT_ID,
            text=f"📊 *דו״ח חודשי אוטומטי — {today.strftime('%B %Y')}*\n\n" + msg,
            parse_mode="Markdown",
        )
    except Exception as e:
        log.error("monthly_report_job error: %s", e)


async def dropout_monitor_job(context):
    """Background job — weekly dropout check."""
    if not TOPAZ_CHAT_ID:
        return
    today = datetime.now(IL_TZ)
    if today.weekday() != 6:  # Sunday only
        return
    try:
        at_risk = dropout_detector.find_at_risk_students(consecutive=3)
        if at_risk:
            msg = dropout_detector.format_at_risk_message(at_risk)
            await context.bot.send_message(
                chat_id=TOPAZ_CHAT_ID,
                text=msg,
                parse_mode="Markdown",
            )
    except Exception as e:
        log.error("dropout_monitor_job error: %s", e)


async def _belt_wizard_finish(message, user_id: str):
    """Generate belt ceremony WhatsApp message and add to calendar."""
    import datetime as _dt
    ss = sheets_sessions.pop(user_id, {})

    child_name    = ss.get("child_name", "")
    belt_color    = ss.get("belt_color", "")
    ceremony_day  = ss.get("ceremony_day", "")
    ceremony_time = ss.get("ceremony_time", "")
    video_link    = ss.get("video_link", "")
    payment_url   = "https://private.invoice4u.co.il/Clearing/Invoice4UClearing.aspx?ProductId=4476&mobileApp=true"

    female_names = {"נועה","שירה","מיה","מאיה","ליאת","יעל","שרה","רחל","לאה","אורית","מורית","דנה",
                    "שני","ענת","לירן","הילה","ליה","ליאה","אביגיל","נגה","הדס","הדר","מרים","נעמי",
                    "עינב","טל","ניצן","כרמל","נורית","גלית","שפרה","נטע","גאיה","תו","רוני"}
    is_female = any(n in child_name for n in female_names)
    suffix_verb = "עשתה" if is_female else "עשה"
    suffix_pass = "עברה" if is_female else "עבר"

    video_part = f"\n*מצורף סרטון למבחן*\n{video_link}" if video_link else ""
    msg = (
        f"היי,\n"
        f"אני שמח לעדכן ש{child_name} {suffix_verb} מבחן לחגורה {belt_color} ו{suffix_pass} בהצלחה! 🥳\n\n"
        f"ביום *{ceremony_day}* נקיים טקס מעבר חגורה כ-10 דקות לקראת סוף האימון.\n"
        f"אתם מוזמנים להגיע, לצלם ולהביא כיבוד בריא (פירות, ירקות וכו׳). *לא חובה*"
        f"{video_part}\n\n"
        f"*עלות חגורה ותעודה - 60 ₪*\n"
        f"*ניתן לרכוש חגורה באמצעות הקישור או להתארגן באופן עצמאי.*\n"
        f"{payment_url}"
    )

    # Find next occurrence of ceremony_day
    cal_status = ""
    try:
        day_map = {"ראשון":6,"שני":0,"שלישי":1,"רביעי":2,"חמישי":3,"שישי":4,"שבת":5}
        today = _dt.date.today()
        target = day_map.get(ceremony_day)
        if target is not None:
            days_ahead = (target - today.weekday()) % 7 or 7
            event_date = today + _dt.timedelta(days=days_ahead)
        else:
            event_date = today

        cal.add_event(
            calendar_name="טקסי מעבר חגורה",
            title=f"טקס מעבר חגורה — {child_name} ({belt_color})",
            event_date=event_date,
            time_str=ceremony_time or None,
            description=f"טקס מעבר חגורה {belt_color} ל{child_name}. כ-10 דקות לפני סוף האימון.",
        )
        time_disp = f" ב-{ceremony_time}" if ceremony_time else ""
        cal_status = f"\n\n✅ *נוסף ביומן:* {event_date.strftime('%d/%m/%Y')}{time_disp} (10 דק׳)"
    except Exception as e:
        cal_status = f"\n\n⚠️ לא הצלחתי להוסיף ליומן: {e}"

    await message.reply_text(msg + cal_status, parse_mode="Markdown",
                              reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לחגורות", callback_data="menu_belts")]]))


async def _create_calendar_event(chat_id, user_id: str, cs: dict, bot):
    """Actually create the event and send confirmation."""
    calendar_sessions.pop(user_id, None)
    try:
        cal.add_event(
            calendar_name=cs["calendar"],
            title=cs["title"],
            event_date=cs["date"],
            time_str=cs.get("time"),
            description="נוצר דרך בוט וולבס ג'ודו"
        )
        emoji = cal.CALENDAR_EMOJI.get(cs["calendar"], "📅")
        date_str = cs["date"].strftime("%d/%m/%Y")
        time_str = f" ב-{cs['time']}" if cs.get("time") else ""
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"✅ *נשמר ביומן!*\n\n"
                f"{emoji} *{cs['calendar']}*\n"
                f"📌 {cs['title']}\n"
                f"📅 {date_str}{time_str} — 10 דקות"
            ),
            parse_mode="Markdown",
        )
    except Exception as e:
        log.error("Calendar event error: %s", e)
        await bot.send_message(chat_id=chat_id, text=f"❌ שגיאה ביצירת האירוע: {e}")


async def handle_calendar_callback(query, user_id: str, calendar_name: str, context):
    """Handle cal_pick_X callback — user picked a calendar."""
    cs = calendar_sessions.get(user_id)
    if not cs:
        await query.answer("אין סשן פעיל")
        return
    cs["calendar"] = calendar_name
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await _create_calendar_event(query.message.chat_id, user_id, cs, context.bot)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    history[user_id] = []
    pending_plans.pop(user_id, None)
    attendance_sessions.pop(user_id, None)
    save_json(HISTORY_FILE, history)
    save_json(PENDING_FILE, pending_plans)
    await update.message.reply_text("🔄 שיחה אופסה.")


async def correction_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/תיקון <טקסט> — מלמד את הבוט תיקון לעתיד."""
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await update.message.reply_text(
            "שלח תיקון כך:\n`/תיקון כשאני אומר X הבוט צריך לעשות Y`",
            parse_mode="Markdown"
        )
        return
    append_correction(f"[תיקון ידני] {text}")
    await update.message.reply_text(f"✅ נשמר! הבוט יזכור:\n_{text}_", parse_mode="Markdown")


async def show_corrections_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/תיקונים — הצג את כל התיקונים השמורים."""
    c = load_corrections()
    if not c:
        await update.message.reply_text("אין תיקונים שמורים עדיין.")
        return
    await update.message.reply_text(f"📝 *תיקונים שמורים:*\n\n{c}", parse_mode="Markdown")


async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cleanup — מחק עמודות ריקות מכל גיליונות הנוכחות."""
    msg = await update.message.reply_text("🧹 מנקה עמודות ריקות מכל הגיליונות...")
    try:
        results = att.cleanup_all_empty_columns()
        lines = ["✅ *ניקוי הושלם*\n"]
        total = 0
        for branch, groups in results.items():
            branch_total = sum(v for v in groups.values() if v > 0)
            total += branch_total
            if branch_total > 0:
                lines.append(f"*{branch}*: נמחקו {branch_total} עמודות")
                for group, count in groups.items():
                    if count > 0:
                        lines.append(f"  • {group}: {count}")
        if total == 0:
            lines.append("לא נמצאו עמודות ריקות 👍")
        await msg.edit_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ שגיאה: {e}")


async def dropouts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """מי פרש — מציג רשימת פורשים לפי סניף."""
    args = context.args
    if not args:
        await update.message.reply_text(
            "שלח: `/פרשו סירקין` — לרשימת פורשים של סניף\n"
            "סניפים: סירקין, נווה ירק, אהרונוביץ, פונקציונלי, חגור",
            parse_mode="Markdown"
        )
        return

    branch = " ".join(args)
    if branch not in att.BRANCH_SHEETS:
        await update.message.reply_text(f"❌ סניף לא מוכר: {branch}")
        return

    try:
        dropouts = att.get_dropouts(branch)
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {e}")
        return

    if not dropouts:
        await update.message.reply_text(f"✅ אין פורשים רשומים ב{branch}")
        return

    by_group = {}
    for d in dropouts:
        by_group.setdefault(d["group"], []).append(d)

    msg = f"🖤 *פורשים — {branch}*\n"
    for group, members in by_group.items():
        msg += f"\n*{group}:*\n"
        for d in members:
            dates = []
            if d.get("start_date"):
                dates.append(f"התחיל: {d['start_date']}")
            if d.get("end_date"):
                dates.append(f"פרש: {d['end_date']}")
            date_str = f" ({', '.join(dates)})" if dates else ""
            msg += f"  • {d['name']}{date_str}\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def design_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/design [סניף] [קבוצה] — apply visual design to sheet(s)."""
    args = context.args
    if len(args) >= 2:
        branch = args[0]
        group = " ".join(args[1:])
        if branch not in att.BRANCH_SHEETS:
            await update.message.reply_text(f"❌ סניף לא מוכר: {branch}")
            return
        if group not in att.BRANCH_GROUPS.get(branch, []):
            await update.message.reply_text(f"❌ קבוצה לא מוכרת: {group}")
            return
        msg = await update.message.reply_text(f"🎨 מעצב {branch} — {group}...")
        try:
            att.apply_sheet_design(branch, group)
            await msg.edit_text(f"✅ עיצוב הוחל על {branch} — {group}")
        except Exception as e:
            await msg.edit_text(f"❌ שגיאה: {e}")
    else:
        # Apply to all sheets
        msg = await update.message.reply_text("🎨 מעצב את כל הגיליונות...")
        errors = []
        count = 0
        for branch, groups in att.BRANCH_GROUPS.items():
            for group in groups:
                try:
                    att.apply_sheet_design(branch, group)
                    count += 1
                except Exception as e:
                    errors.append(f"{branch}/{group}: {e}")
        # Also design training plans sheet
        try:
            plans_result = tp.design_all_tabs()
            count_plans = plans_result.count("✅")
            result = f"✅ עיצוב הוחל על {count} גיליונות נוכחות + {count_plans} תוכניות אימון"
        except Exception as e:
            result = f"✅ עיצוב הוחל על {count} גיליונות"
            errors.append(f"תוכניות אימון: {e}")
        if errors:
            result += "\n\n⚠️ שגיאות:\n" + "\n".join(errors)
        await msg.edit_text(result)


# ─────────────────────────────────────────────
# DATA CONTEXT — הזרקת נתונים חיים לקלוד
# ─────────────────────────────────────────────

def _build_data_context(text: str) -> str:
    """Build live-data context string based on what the user is asking about."""
    parts = []
    t = text

    absence_keywords = ("נעדר", "היעדרות", "לא הגיע", "חסר", "חסרים", "מי לא")
    student_keywords = ("ספורטאים", "ילדים", "תלמידים", "כמה יש", "רשימה", "מי יש")
    camp_keywords = ("מחנה",)
    lyla_keywords = ("לילה יפני",)
    attendance_stat_keywords = ("נוכחות", "אחוז", "סטטיסטיקה", "דוח")

    # Full-day plan context — inject schedule when asking for a full day
    full_day_keywords = ("יום מלא", "כל הקבוצות", "כל הסניף", "לסניף", "לכל")
    if any(k in t for k in full_day_keywords):
        try:
            from datetime import date as _date
            today = _date.today()
            detected_branch = next((b for b in tp.BRANCH_TABS if b in t), None)
            # Try to detect date from text
            _b, plan_date = tp.detect_branch_and_date(t)
            detected_branch = detected_branch or _b
            check_date = plan_date or today
            if detected_branch:
                groups = ws.groups_for_branch_on_date(detected_branch, check_date)
                if groups:
                    day_he = ws.day_name(check_date)
                    lines = [f"לוז {detected_branch} יום {day_he} {check_date.day}/{check_date.month}:"]
                    for g in groups:
                        cancelled_note = " ← בוטל" if g.get("cancelled") else ""
                        lines.append(f"  {g['time']} {g['name']}{cancelled_note}")
                    parts.append("\n".join(lines))
        except Exception:
            pass

    if any(k in t for k in absence_keywords):
        try:
            absence_log_data = load_json(Path("absence_log.json"), {})
            streaks = []
            for name, records in absence_log_data.items():
                recent = records[-5:]
                consecutive = 0
                for r in reversed(recent):
                    if r.get("absent"):
                        consecutive += 1
                    else:
                        break
                total_absent = sum(1 for r in records if r.get("absent"))
                total = len(records)
                if consecutive >= 2 or total_absent >= 3:
                    streaks.append((name, consecutive, total_absent, total))
            streaks.sort(key=lambda x: (-x[1], -x[2]))
            if streaks:
                lines = ["ספורטאים עם היעדרויות בולטות:"]
                for name, consec, absent, total in streaks[:15]:
                    pct = int(absent / total * 100) if total else 0
                    flag = f" ⚠️ {consec} ברצף" if consec >= 2 else ""
                    lines.append(f"  {name}: {absent}/{total} ({pct}%){flag}")
                parts.append("\n".join(lines))
            else:
                parts.append("אין ספורטאים עם היעדרויות חריגות.")
        except Exception:
            pass

    if any(k in t for k in student_keywords):
        try:
            total_by_branch = {}
            for branch, groups in att.BRANCH_GROUPS.items():
                service = att._get_service()
                count = 0
                for group in groups:
                    try:
                        students = att.get_students(service, att.BRANCH_SHEETS[branch], group)
                        count += len(students)
                    except Exception:
                        pass
                total_by_branch[branch] = count
            total = sum(total_by_branch.values())
            lines = [f"סה\"כ ספורטאים פעילים: {total}"]
            for b, n in sorted(total_by_branch.items(), key=lambda x: -x[1]):
                lines.append(f"  {b}: {n}")
            parts.append("\n".join(lines))
        except Exception:
            pass

    if any(k in t for k in camp_keywords):
        try:
            s = camp.get_stats()
            lines = [f"מחנה קיץ: {s['total']} ילדים"]
            for w, n in sorted(s['by_week'].items()):
                lines.append(f"  {w}: {n}")
            parts.append("\n".join(lines))
        except Exception:
            pass

    if any(k in t for k in lyla_keywords):
        try:
            s = lyla.get_stats()
            lines = [f"לילה יפני: {s['total']} משתתפים"]
            for b, n in sorted(s['by_branch'].items(), key=lambda x: -x[1]):
                lines.append(f"  {b}: {n}")
            parts.append("\n".join(lines))
        except Exception:
            pass

    if any(k in t for k in ("תחרות", "תחרויות", "גביע", "אליפות", "מדליה")):
        try:
            s = comp_sheet.get_stats()
            lines = [f"תחרויות: {s['total_competitions']} תחרויות, {s['total_participants']} משתתפים"]
            for comp_name, n in sorted(s['by_competition'].items(), key=lambda x: -x[1]):
                lines.append(f"  {comp_name}: {n} ספורטאים")
            if s['medals']:
                lines.append("מדליות: " + ", ".join(f"{k}: {v}" for k, v in s['medals'].items()))
            parts.append("\n".join(lines))
        except Exception:
            pass

    # Contacts context — parent lookup
    contact_triggers = ("טלפון", "הורה", "הורים", "אנשי קשר", "שלח הודעה", "הכן הודעה",
                        "מי ההורה", "ווטסאפ", "whatsapp", "sms", "להורה")
    if any(k in t.lower() for k in contact_triggers):
        try:
            cs = contacts_db.stats()
            lines = [f"אנשי קשר: {cs['total']} סה\"כ"]
            for b, n in cs["by_branch"].items():
                lines.append(f"  {b}: {n}")
            parts.append("\n".join(lines))
            # If specific athlete name mentioned, look them up
            import re as _re
            # Heuristic: look for Hebrew words that could be athlete names (2+ words)
            words = _re.findall(r'[א-ת]{2,}', t)
            if len(words) >= 2:
                # Try two-word combos
                for i in range(len(words) - 1):
                    candidate = f"{words[i]} {words[i+1]}"
                    results = contacts_db.find_parent(candidate)
                    if results:
                        p = results[0]
                        parts.append(f"הורה של {candidate}: {p['parent_name']} — {p['phone']}")
                        break
        except Exception:
            pass

    # Payment + contacts cross-reference
    payment_contact_triggers = ("לא שילם", "חייב", "חוב", "תשלום חסר", "לא שולם", "מי לא שילם")
    if any(k in t for k in payment_contact_triggers):
        try:
            cs = contacts_db.stats()
            parts.append(f"נתוני אנשי קשר זמינים: {cs['total']} הורים — ניתן לשלוח תזכורות")
        except Exception:
            pass

    # Student name detection — 2 Hebrew words that match a known student
    import re as _re2
    name_trigger_words = ("תן לי פרטים", "פרטים על", "כרטיס של", "מי זה", "מה עם", "מה קורה עם")
    has_name_trigger = any(k in t for k in name_trigger_words)
    # Also trigger for short messages that look like a name (2-4 Hebrew words, no other content)
    looks_like_name = bool(_re2.match(r"^[א-ת]{2,}(\s[א-ת]{2,}){1,3}$", t.strip()))
    if has_name_trigger or looks_like_name:
        try:
            # Extract potential name: last 2-3 Hebrew words
            words = _re2.findall(r'[א-ת]{2,}', t)
            candidates = []
            if len(words) >= 2:
                candidates.append(" ".join(words[-2:]))
            if len(words) >= 3:
                candidates.append(" ".join(words[-3:]))
            for candidate in candidates:
                data = _search_student_everywhere(candidate)
                if data:
                    parts.append(_format_student_card_full(data))
                    break
        except Exception:
            pass

    return "\n\n".join(parts)


async def _calendar_query(update: Update, context: ContextTypes.DEFAULT_TYPE, query_text: str):
    """Fetch calendar events for query_text and reply with Claude analysis."""
    # Support both message and callback contexts
    if update.callback_query:
        chat = update.callback_query.message.chat
        reply_fn = update.callback_query.message.reply_text
    else:
        chat = update.message.chat
        reply_fn = update.message.reply_text
    await chat.send_action("typing")
    try:
        date_from, date_to = cal.parse_date_range_hebrew(query_text)
        events = cal.get_events_range(date_from, date_to)
        # Limit to 40 events max to avoid token overflow
        events = events[:40]
        events_text = cal.format_events_for_claude(events, date_from, date_to)
        from datetime import date as _date
        _today = _date.today()
        _day_names = ["שני","שלישי","רביעי","חמישי","שישי","שבת","ראשון"]
        _today_str = f"יום {_day_names[_today.weekday()]} {_today.strftime('%d/%m/%Y')}"
        system = (
            f"אתה עוזר אישי של טופז זבארי, מאמן ג'ודו. "
            f"היום הוא {_today_str}. "
            "קיבלת נתונים מ-Google Calendar שלו. "
            "סכם את האירועים בצורה מסודרת ושימושית. "
            "הדגש אימוני ג'ודו, משימות דחופות ואירועים חשובים. "
            "חשוב: כתוב את יום השבוע והתאריך בדיוק כפי שמופיע בנתונים — אל תחשב לבד. "
            "ענה בעברית, קצר וברור."
        )
        # Fresh Claude call — no history, to avoid token overflow
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": events_text}],
        )
        reply = response.content[0].text
    except Exception as e:
        log.error("Calendar query error: %s", e)
        reply = f"❌ שגיאה בשליפת היומן: {e}"
    chunks = [reply[i:i+4096] for i in range(0, len(reply), 4096)]
    for chunk in chunks:
        await reply_fn(chunk)


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _calendar_query(update, context, "היום")


async def tomorrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _calendar_query(update, context, "מחר")


async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _calendar_query(update, context, "השבוע")


async def month_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _calendar_query(update, context, "החודש")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stats — סטטיסטיקה מהירה על המועדון."""
    msg = await update.message.reply_text("📊 טוען נתונים...")
    lines = ["📊 *סטטיסטיקת וולבס ג׳ודו*\n"]

    # Absence alerts
    try:
        absence_log_stats = load_json(Path("absence_log.json"), {})
        alerts = []
        for name, records in absence_log_stats.items():
            recent = records[-3:]
            if len(recent) >= 3 and all(r.get("absent") for r in recent):
                alerts.append(name)
        if alerts:
            lines.append(f"⚠️ *{len(alerts)} ספורטאים עם 3+ היעדרויות ברצף:*")
            for n in alerts[:10]:
                lines.append(f"  • {n}")
            lines.append("")
    except Exception:
        pass

    # Camp stats
    try:
        s = camp.get_stats()
        lines.append(f"🏕 *מחנה קיץ:* {s['total']} ילדים")
        for w, n in sorted(s['by_week'].items()):
            lines.append(f"  {w}: {n}")
        lines.append("")
    except Exception:
        pass

    # Lyla stats
    try:
        s = lyla.get_stats()
        lines.append(f"🌸 *לילה יפני:* {s['total']} משתתפים")
        lines.append("")
    except Exception:
        pass

    # Today's schedule
    schedule = att.get_todays_schedule()
    if schedule:
        day_names = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
        day = day_names[__import__("datetime").datetime.now(IL_TZ).weekday()]
        lines.append(f"📅 *היום ({day}):*")
        for branch, group, time in schedule:
            lines.append(f"  {time} — {branch} {group}")

    await msg.edit_text("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────
# SHEETS — מחנה קיץ + לילה יפני
# ─────────────────────────────────────────────

def camp_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 רשימה", callback_data="camp_list"),
         InlineKeyboardButton("📊 סטטיסטיקה", callback_data="camp_stats")],
        [InlineKeyboardButton("➕ הוסף ילד", callback_data="camp_add"),
         InlineKeyboardButton("✏️ עדכן פרטים", callback_data="camp_upd")],
        [InlineKeyboardButton("🎨 עיצב גיליון", callback_data="camp_format")],
    ])


def lyla_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 רשימה", callback_data="lyla_list"),
         InlineKeyboardButton("📊 סטטיסטיקה", callback_data="lyla_stats")],
        [InlineKeyboardButton("➕ הוסף משתתף", callback_data="lyla_add"),
         InlineKeyboardButton("🎨 עיצב גיליון", callback_data="lyla_format")],
    ])


def branch_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("סירקין", callback_data=f"{prefix}סירקין"),
         InlineKeyboardButton("נווה ירק", callback_data=f"{prefix}נווה ירק")],
        [InlineKeyboardButton("חגור", callback_data=f"{prefix}חגור"),
         InlineKeyboardButton("אהרונוביץ", callback_data=f"{prefix}אהרונוביץ")],
    ])


def week_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("שבועיים", callback_data="camp_week_שבועיים"),
        InlineKeyboardButton("שבוע שני", callback_data="camp_week_שבוע שני"),
    ]])


def update_field_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("מידת חולצה", callback_data="camp_field_shirt"),
         InlineKeyboardButton("תשלום", callback_data="camp_field_paid")],
        [InlineKeyboardButton("צהרון", callback_data="camp_field_lunch"),
         InlineKeyboardButton("שבוע", callback_data="camp_field_notes")],
        [InlineKeyboardButton("כיתה", callback_data="camp_field_grade"),
         InlineKeyboardButton("סניף", callback_data="camp_field_branch")],
    ])


def yesno_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ כן", callback_data=f"{prefix}כן"),
        InlineKeyboardButton("❌ לא", callback_data=f"{prefix}לא"),
    ]])


def _camp_list_text():
    students = camp.get_students()
    if not students:
        return "📋 אין רשומים עדיין."
    by_week: dict[str, list] = {}
    for s in students:
        w = s['notes'] or 'לא ידוע'
        by_week.setdefault(w, []).append(s)
    lines = [f"📋 *מחנה קיץ — {len(students)} ילדים*\n"]
    for week, kids in sorted(by_week.items()):
        lines.append(f"*{week} ({len(kids)}):*")
        by_grade: dict[str, list] = {}
        for k in kids:
            by_grade.setdefault(k['grade'] or '?', []).append(k['name'])
        for grade in sorted(by_grade, key=lambda g: camp.GRADE_ORDER.get(g, 99)):
            names = ', '.join(sorted(by_grade[grade]))
            lines.append(f"  כיתה {grade}: {names}")
        lines.append("")
    return '\n'.join(lines)


def _lyla_list_text():
    students = lyla.get_students()
    if not students:
        return "📋 אין משתתפים עדיין."
    by_branch: dict[str, list] = {}
    for s in students:
        by_branch.setdefault(s['branch'] or 'לא ידוע', []).append(s)
    lines = [f"🌸 *לילה יפני — {len(students)} משתתפים*\n"]
    for branch, kids in sorted(by_branch.items()):
        lines.append(f"*{branch} ({len(kids)}):*")
        for k in sorted(kids, key=lambda x: (lyla.GRADE_ORDER.get(x['grade'], 99), x['name'])):
            lines.append(f"  כיתה {k['grade']}: {k['name']}")
        lines.append("")
    return '\n'.join(lines)


async def camp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = camp.get_stats()
    await update.message.reply_text(
        f"🏕 *מחנה קיץ — {stats['total']} ילדים רשומים*",
        parse_mode="Markdown",
        reply_markup=camp_menu_keyboard(),
    )


async def lyla_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = lyla.get_stats()
    await update.message.reply_text(
        f"🌸 *לילה יפני — {stats['total']} משתתפים*",
        parse_mode="Markdown",
        reply_markup=lyla_menu_keyboard(),
    )


async def handle_sheets_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles text input during camp/lyla flows. Returns True if consumed."""
    user_id = str(update.effective_user.id)
    ss = sheets_sessions.get(user_id)
    if not ss:
        return False

    text = update.message.text.strip()
    step = ss.get('step')

    # ── Full-day plan — waiting for plan text ───────────────────────────────────
    if step == "fd_waiting_plan":
        from datetime import date as _date
        branch    = ss.get("branch", "")
        plan_date = _date.fromisoformat(ss.get("plan_date", _date.today().isoformat()))
        sheets_sessions.pop(user_id, None)
        pending_plans[user_id] = {
            "reply": text, "original": text,
            "branch": branch, "plan_date": plan_date.isoformat(),
        }
        save_json(PENDING_FILE, pending_plans)
        await _plan_offer_save(update, user_id, text, branch, plan_date)
        return True

    # ── Parent message — absence ─────────────────────────────────────────────────
    if step == "pm_absence_name":
        name = text.strip()
        ss["name"] = name
        ss["step"] = "pm_absence_branch"
        sheets_sessions[user_id] = ss
        rows = [[InlineKeyboardButton(b, callback_data=f"pm_abs_br|{b}")] for b in contacts_db.CONTACT_FILES]
        rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
        await update.message.reply_text(
            f"✅ {name}\n\nאיזה סניף?",
            reply_markup=InlineKeyboardMarkup(rows)
        )
        return True

    # ── Parent message — payment ─────────────────────────────────────────────────
    if step == "pm_payment_name":
        name = text.strip()
        ss["name"] = name
        ss["step"] = "pm_payment_branch"
        sheets_sessions[user_id] = ss
        rows = [[InlineKeyboardButton(b, callback_data=f"pm_pay_br|{b}")] for b in contacts_db.CONTACT_FILES]
        rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
        await update.message.reply_text(
            f"✅ {name}\n\nאיזה סניף?",
            reply_markup=InlineKeyboardMarkup(rows)
        )
        return True

    # ── Plan edit — content input ─────────────────────────────────────────────────
    if step == "plan_edit_content":
        import re as _re
        from datetime import date as _date
        branch    = ss.get("branch", "")
        group     = ss.get("group", "")
        plan_date = _date.fromisoformat(ss.get("plan_date", _date.today().isoformat()))
        sheets_sessions.pop(user_id, None)

        # Parse lines / bullets from text
        items = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            line = _re.sub(r'^[•\-\*\d\.]+\s*', '', line).strip()
            if line:
                items.append(line)

        if not items:
            await update.message.reply_text("❌ לא מצאתי תוכן. נסה שוב.")
            return True

        await update.message.reply_text(
            f"⏳ Claude מסדר את התוכנית ושומר לגיליון..."
        )
        try:
            result = tp.save_plan_to_sheet(branch, group, plan_date, items)
            await update.message.reply_text(
                f"✅ *נשמר!*\n{result}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "📋 פתח גיליון",
                        url="https://docs.google.com/spreadsheets/d/1hi073ueyzdzEjzhP6a3ZgTPpeZDNzH2g2rKPj-L8a6I/edit"
                    )
                ]])
            )
        except Exception as e:
            await update.message.reply_text(f"❌ שגיאה: {e}")
        return True

    # ── Multi-group plan — manual date input ─────────────────────────────────────
    if step == "mg_pick_date":
        from datetime import date as _date
        import re as _re
        d_match = _re.search(r'(\d{1,2})[/.](\d{1,2})', text)
        if d_match:
            day, month = int(d_match.group(1)), int(d_match.group(2))
            try:
                plan_date = _date(_date.today().year, month, day)
                branch = ss.get("branch", "")
                groups = ss.get("groups", [])
                sheets_sessions.pop(user_id, None)
                await update.message.reply_text("⏳ שומר...")
                result = tp.save_multigroup_plan(branch, plan_date, groups)
                await update.message.reply_text(f"✅ *נשמר!*\n\n{result}", parse_mode="Markdown")
            except Exception as e:
                await update.message.reply_text(f"❌ שגיאה: {e}")
        else:
            await update.message.reply_text("שלח תאריך בפורמט: 27/6")
        return True

    # ── Payment edit input ────────────────────────────────────────────────────────
    if step == "pay_edit_input":
        key = ss.get("pay_key", "")
        p = pending_payments.get(key)
        parts = [x.strip() for x in text.split("|")]
        if len(parts) < 3 or not p:
            await update.message.reply_text("❌ פורמט לא תקין. נסה: `שם | חודש | סכום`", parse_mode="Markdown")
            return True
        name, month, amount = parts[0], parts[1], parts[2]
        student = payments_sheet.find_student(name)
        if not student:
            await update.message.reply_text(f"⚠️ לא מצאתי ספורטאי בשם *{name}*. בדוק בגיליון ידנית.", parse_mode="Markdown")
            sheets_sessions.pop(user_id, None)
            pending_payments.pop(key, None)
            return True
        try:
            payments_sheet.update_payment(student["row"], month, amount)
            await update.message.reply_text(
                f"✅ *עודכן!*\n• {student['full_name']} | {month} | {amount}₪",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ שגיאה: {e}")
        sheets_sessions.pop(user_id, None)
        pending_payments.pop(key, None)
        return True

    # ── Belt wizard — name input ──────────────────────────────────────────────────
    if step == 'belt_wizard_name':
        child_name = text.strip()
        ss["child_name"] = child_name
        ss["step"] = "belt_wizard_color"
        sheets_sessions[user_id] = ss
        COLORS = [("לבנה","bw_color|לבנה"), ("צהובה","bw_color|צהובה"), ("כתומה","bw_color|כתומה"),
                  ("ירוקה","bw_color|ירוקה"), ("כחולה","bw_color|כחולה"), ("חומה","bw_color|חומה"), ("שחורה","bw_color|שחורה")]
        rows = []
        for i in range(0, len(COLORS), 3):
            rows.append([InlineKeyboardButton(c[0], callback_data=c[1]) for c in COLORS[i:i+3]])
        rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
        await update.message.reply_text(
            f"✅ *{child_name}*\n\n🥋 *איזו חגורה?*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows)
        )
        return True

    # ── Belt wizard — video link input ────────────────────────────────────────────
    if step == 'belt_wizard_link':
        ss["video_link"] = text.strip() if text.startswith("http") else ""
        sheets_sessions[user_id] = ss
        await _belt_wizard_finish(update.message, user_id)
        return True

    if step == 'belt_msg_details':
        sheets_sessions.pop(user_id, None)
        # Parse: name, belt_color, day, branch, group[, link]
        parts = [p.strip() for p in text.replace("،", ",").split(",")]
        if len(parts) < 3:
            await update.message.reply_text(
                "❌ פורמט: `שם, צבע, יום, סניף, קבוצה, קישור (אופציונלי)`",
                parse_mode="Markdown")
            return True

        child_name   = parts[0]
        belt_color   = parts[1]
        ceremony_day = parts[2]
        branch       = parts[3] if len(parts) > 3 else ""
        group        = parts[4] if len(parts) > 4 else ""
        # link is last part if it starts with http
        video_link   = ""
        for p in parts[3:]:
            if p.startswith("http"):
                video_link = p
                break

        # Lookup training end time from schedule
        SCHEDULE = {
            # (branch_keyword, group_keyword, day) -> end_time HH:MM
            ("חגור",    "ד",      "ראשון"): "16:30",
            ("חגור",    "א",      "ראשון"): "17:15",
            ("חגור",    "גנים",   "ראשון"): "18:00",
            ("סירקין",  "ד",      "שני"):   "15:30",
            ("סירקין",  "ג",      "שני"):   "16:30",
            ("סירקין",  "א",      "שני"):   "17:15",
            ("סירקין",  "בוגרים", "שני"):   "19:30",
            ("סירקין",  "ז",      "שני"):   "19:30",
            ("נווה ירק","גנים",   "שלישי"): "16:45",
            ("נווה ירק","ג",      "שלישי"): "17:45",
            ("נווה ירק","א",      "שלישי"): "18:30",
            ("אהרונוביץ","א",     "רביעי"): "14:50",
            ("אהרונוביץ","ג",     "רביעי"): "14:50",
            ("סירקין",  "ז-ח",   "רביעי"): "17:15",
            ("סירקין",  "ט",     "רביעי"): "18:15",
            ("סירקין",  "ב-ד",   "רביעי"): "19:15",
            ("סירקין",  "ה-ז",   "רביעי"): "20:00",
            ("סירקין",  "ד",      "חמישי"): "15:30",
            ("סירקין",  "ג",      "חמישי"): "16:30",
            ("סירקין",  "א",      "חמישי"): "17:15",
            ("סירקין",  "גנים",   "חמישי"): "18:00",
            ("סירקין",  "בוגרים", "חמישי"): "19:30",
            ("סירקין",  "ז",      "חמישי"): "19:30",
            ("סירקין",  "נבחרת",  "שישי"):  "15:00",
            ("גבעת",    "בוגרת",  "שישי"):  "17:45",
        }

        end_time = None
        for (b_key, g_key, d_key), etime in SCHEDULE.items():
            if b_key in branch and g_key in group and d_key in ceremony_day:
                end_time = etime
                break

        # Compute ceremony time = 10 min before end
        ceremony_time = None
        if end_time:
            h, m = map(int, end_time.split(":"))
            total = h * 60 + m - 10
            ceremony_time = f"{total // 60:02d}:{total % 60:02d}"

        payment_url = "https://private.invoice4u.co.il/Clearing/Invoice4UClearing.aspx?ProductId=4476&mobileApp=true"
        gender_suffix = "ה" if any(n in child_name for n in ["נועה","שירה","מיה","מאיה","רוני","ליאת","יעל","שרה","רחל","לאה","אורית","מורית","דנה","שני","ענת","לירן","הילה","ליה","ליאה","אביגיל","נגה","הדס","הדר","מרים","נעמי","עינב","טל","ניצן","כרמל","נורית","גלית","שפרה"]) else ""
        suffix_verb = "עשתה" if gender_suffix else "עשה"
        suffix_pass = "עברה" if gender_suffix else "עבר"

        video_part = f"\n*מצורף סרטון למבחן*\n{video_link}" if video_link else ""

        msg = (
            f"היי,\n"
            f"אני שמח לעדכן ש{child_name} {suffix_verb} מבחן לחגורה {belt_color} ו{suffix_pass} בהצלחה! 🥳\n\n"
            f"ביום *{ceremony_day}* נקיים טקס מעבר חגורה כ-10 דקות לקראת סוף האימון.\n"
            f"אתם מוזמנים להגיע, לצלם ולהביא כיבוד בריא (פירות, ירקות וכו׳). *לא חובה*"
            f"{video_part}\n\n"
            f"*עלות חגורה ותעודה - 60 ₪*\n"
            f"*ניתן לרכוש חגורה באמצעות הקישור או להתארגן באופן עצמאי.*\n"
            f"{payment_url}"
        )

        # Auto-add to Google Calendar
        from datetime import date as date_cls
        cal_status = ""
        try:
            # Find next occurrence of ceremony_day
            day_map = {"ראשון": 6, "שני": 0, "שלישי": 1, "רביעי": 2, "חמישי": 3, "שישי": 4, "שבת": 5}
            today = date_cls.today()
            target_weekday = next((v for k, v in day_map.items() if k in ceremony_day), None)
            if target_weekday is not None:
                days_ahead = (target_weekday - today.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7
                event_date = today + __import__("datetime").timedelta(days=days_ahead)
            else:
                event_date = today

            cal.add_event(
                calendar_name="טקסי מעבר חגורה",
                title=f"טקס מעבר חגורה — {child_name} ({belt_color})",
                event_date=event_date,
                time_str=ceremony_time or None,
                description=f"טקס מעבר חגורה {belt_color} ל{child_name}. כ-10 דקות לפני סוף האימון.",
            )
            time_display = f" ב-{ceremony_time}" if ceremony_time else ""
            cal_status = f"\n\n✅ *נוסף ביומן:* {event_date.strftime('%d/%m/%Y')}{time_display}"
        except Exception as e:
            cal_status = f"\n\n⚠️ לא הצלחתי להוסיף ליומן: {e}"

        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה", callback_data="menu_belts")]])
        await update.message.reply_text(msg + cal_status, parse_mode="Markdown", reply_markup=markup)
        return True

    # ── Belt calendar event ───────────────────────────────────────────────────────
    if step == 'belt_cal_date':
        sheets_sessions.pop(user_id, None)
        import re as _re
        from datetime import date as date_cls
        d_match = _re.search(r'(\d{1,2})[/.](\d{1,2})', text)
        if not d_match:
            await update.message.reply_text("❌ לא הבנתי תאריך. נסה: `27/6`", parse_mode="Markdown")
            return True
        day, month = int(d_match.group(1)), int(d_match.group(2))
        event_date = date_cls(date_cls.today().year, month, day)

        child_name    = ss.get("child_name", "")
        belt_color    = ss.get("belt_color", "")
        ceremony_day  = ss.get("ceremony_day", "")
        ceremony_time = ss.get("ceremony_time") or None
        title         = f"טקס מעבר חגורה — {child_name} ({belt_color})"

        try:
            cal.add_event(
                calendar_name="טקסי מעבר חגורה",
                title=title,
                event_date=event_date,
                time_str=ceremony_time,
                description=f"טקס מעבר חגורה {belt_color} ל{child_name}. כ-10 דקות לפני סוף האימון.",
            )
            time_display = f" ב-{ceremony_time}" if ceremony_time else ""
            await update.message.reply_text(
                f"✅ נוסף ליומן *טקסי מעבר חגורה*!\n"
                f"📅 {title}\n"
                f"🗓 {event_date.strftime('%d/%m/%Y')}{time_display}",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ שגיאה בהוספה ליומן: {e}")
        return True

    # ── Save direct from original text ──────────────────────────────────────────
    if step == 'save_direct_date':
        from datetime import date as date_cls
        import re as _re
        d_match = _re.search(r'(\d{1,2})[/.](\d{1,2})', text)
        if "היום" in text:
            plan_date = date_cls.today()
        elif "מחר" in text:
            from datetime import timedelta
            plan_date = date_cls.today() + timedelta(days=1)
        elif d_match:
            plan_date = date_cls(date_cls.today().year, int(d_match.group(2)), int(d_match.group(1)))
        else:
            await update.message.reply_text("❌ לא הבנתי תאריך. נסה: `26/6` או `היום`", parse_mode="Markdown")
            return True

        original = ss.get("original", "")
        await update.message.reply_text("⏳ שומר בגיליון...")

        # Ask Claude to extract structured plan items for the sheet
        import anthropic as _anthropic
        _client = _anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        extract_prompt = f"""המשתמש שלח תוכנית אימון לגיליון תוכניות האימון.
הגיליון מחולק לשורות: חימום, תרגול א, תרגול ב, קרבות א, קרבות ב, כוח, הערות, סיום.
החזר רשימה של בדיוק 8 פריטים (שורה אחת לכל תא בגיליון), מופרדים בשורות.
ללא מספור, ללא כותרות, רק הטקסט של כל שורה. אם אין תוכן לשורה — כתוב -.

הטקסט:
{original}"""
        resp = _client.messages.create(
            model="claude-sonnet-4-6", max_tokens=512,
            messages=[{"role": "user", "content": extract_prompt}]
        )
        items_text = resp.content[0].text.strip()
        plan_items = [line.strip() for line in items_text.splitlines() if line.strip()]
        plan_items = [i if i != "-" else "" for i in plan_items]

        # Detect branch from original text
        branch = "נבחרת"
        group = "נבחרת"
        for b in ["סירקין", "חגור", "נווה ירק", "אהרונוביץ", "פונקציונלי", "נבחרת", "איפון פייט"]:
            if b in original:
                branch = b
                group = b
                break

        try:
            result = tp.save_plan_to_sheet(branch, group, plan_date, plan_items)
            await update.message.reply_text(result)
        except Exception as e:
            await update.message.reply_text(f"❌ שגיאה: {e}")
        sheets_sessions.pop(user_id, None)
        return True

    # ── Save plan to sheet flow ──
    if step == 'save_plan_date':
        from datetime import date as date_cls
        import re as _re
        d_match = _re.search(r'(\d{1,2})[/.](\d{1,2})', text)
        if "היום" in text:
            plan_date = date_cls.today()
        elif d_match:
            plan_date = date_cls(date_cls.today().year, int(d_match.group(2)), int(d_match.group(1)))
        else:
            await update.message.reply_text("❌ לא הבנתי תאריך. נסה: `26/6`", parse_mode="Markdown")
            return True
        branch = ss["branch"]
        group = ss["group"]
        csv_text = ss.get("csv", "")
        # Extract plan items from CSV
        lines = [l for l in csv_text.splitlines() if l.strip() and not l.startswith("שעה")]
        items = []
        for line in lines:
            parts = line.split(",")
            if len(parts) >= 3:
                items.append(parts[2].strip())
        if not items:
            await update.message.reply_text("❌ לא מצאתי תוכן לשמירה")
            sheets_sessions.pop(user_id, None)
            return True
        try:
            result = tp.save_plan_to_sheet(branch, group, plan_date, items)
            await update.message.reply_text(result)
        except Exception as e:
            await update.message.reply_text(f"❌ שגיאה בשמירה: {e}")
        sheets_sessions.pop(user_id, None)
        return True

    if step == 'save_plan_meta':
        parts = [p.strip() for p in text.replace("|", ",").split(",")]
        if len(parts) >= 3:
            ss["branch"] = parts[0]
            ss["group"] = parts[1]
            ss["step"] = "save_plan_date"
            date_str = parts[2]
            ss["date_text"] = date_str
            await update.message.reply_text(f"📅 שומר ל-*{parts[0]}* {parts[1]}... תאריך: {date_str}", parse_mode="Markdown")
            # Re-trigger with date
            update.message._text = date_str
            return await handle_sheets_text(update, context)
        await update.message.reply_text("❌ פורמט: `סניף | קבוצה | תאריך`", parse_mode="Markdown")
        return True

    # ── Camp add flow ──
    if step == 'camp_add_name':
        ss['name'] = text
        ss['step'] = 'camp_add_grade'
        await update.message.reply_text("📚 *כיתה?* (לדוגמה: א, ג, ז)", parse_mode="Markdown")
        return True

    if step == 'camp_add_grade':
        ss['grade'] = text
        ss['step'] = 'camp_add_branch'
        await update.message.reply_text("🏠 *סניף?*", parse_mode="Markdown",
                                         reply_markup=branch_keyboard("camp_branch_"))
        return True

    # ── Camp update flow ──
    if step == 'camp_upd_name':
        students = camp.get_students()
        names = [s['name'] for s in students]
        # fuzzy match
        match = next((n for n in names if text in n or n in text), None)
        if not match:
            await update.message.reply_text(
                f"❌ לא מצאתי *{text}* ברשימה.\nנסה שוב או שלח *ביטול*:",
                parse_mode="Markdown"
            )
            return True
        ss['target'] = match
        ss['step'] = 'camp_upd_field'
        await update.message.reply_text(
            f"✏️ *{match}* — מה לעדכן?",
            parse_mode="Markdown",
            reply_markup=update_field_keyboard(),
        )
        return True

    if step == 'camp_upd_value':
        field = ss.get('field')
        target = ss.get('target')
        try:
            found = camp.update_student(target, field, text)
        except Exception as e:
            await update.message.reply_text(f"❌ שגיאה: {e}")
            sheets_sessions.pop(user_id, None)
            return True
        sheets_sessions.pop(user_id, None)
        if found:
            await update.message.reply_text(f"✅ *{target}* עודכן.", parse_mode="Markdown",
                                             reply_markup=camp_menu_keyboard())
        else:
            await update.message.reply_text(f"❌ לא נמצא {target}", reply_markup=camp_menu_keyboard())
        return True

    # ── Lyla add flow ──
    if step == 'lyla_add_name':
        ss['name'] = text
        ss['step'] = 'lyla_add_grade'
        await update.message.reply_text("📚 *כיתה?*", parse_mode="Markdown")
        return True

    if step == 'lyla_add_grade':
        ss['grade'] = text
        ss['step'] = 'lyla_add_branch'
        await update.message.reply_text("🏠 *סניף?*", parse_mode="Markdown",
                                         reply_markup=branch_keyboard("lyla_branch_"))
        return True

    if text == 'ביטול':
        sheets_sessions.pop(user_id, None)
        await update.message.reply_text("❌ בוטל.")
        return True

    return False


async def handle_sheets_callback(query, user_id: str, action: str, context) -> bool:
    """Handle camp_* and lyla_* callbacks. Returns True if consumed."""
    await query.answer()

    # ── Camp main menu actions ──
    if action == 'camp_list':
        text = _camp_list_text()
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=camp_menu_keyboard())
        return True

    if action == 'camp_stats':
        s = camp.get_stats()
        lines = [f"📊 *מחנה קיץ — סטטיסטיקה*\n", f"סה\"כ: *{s['total']}* ילדים"]
        for w, n in sorted(s['by_week'].items()):
            lines.append(f"  {w}: {n}")
        lines.append("")
        for b, n in sorted(s['by_branch'].items(), key=lambda x: -x[1]):
            lines.append(f"  {b}: {n}")
        if s['paid']:
            lines.append(f"\n✅ שילמו: {s['paid']}")
        if s['lunch']:
            lines.append(f"🍱 צהרון: {s['lunch']}")
        await query.edit_message_text('\n'.join(lines), parse_mode="Markdown",
                                      reply_markup=camp_menu_keyboard())
        return True

    if action == 'camp_add':
        sheets_sessions[user_id] = {'step': 'camp_add_name', 'mode': 'camp'}
        await query.edit_message_text("➕ *שם הילד?*", parse_mode="Markdown")
        return True

    if action == 'camp_upd':
        sheets_sessions[user_id] = {'step': 'camp_upd_name', 'mode': 'camp'}
        await query.edit_message_text("✏️ *שם הילד לעדכון?*", parse_mode="Markdown")
        return True

    if action == 'camp_format':
        await query.edit_message_text("🎨 מעצב גיליון...")
        try:
            camp.format_sheet()
            await query.edit_message_text("✅ גיליון מחנה קיץ עוצב מחדש.",
                                           reply_markup=camp_menu_keyboard())
        except Exception as e:
            await query.edit_message_text(f"❌ שגיאה: {e}", reply_markup=camp_menu_keyboard())
        return True

    # ── Camp branch pick (add flow) ──
    if action.startswith('camp_branch_'):
        branch = action[len('camp_branch_'):]
        ss = sheets_sessions.get(user_id, {})
        ss['branch'] = branch
        ss['step'] = 'camp_add_week'
        sheets_sessions[user_id] = ss
        await query.edit_message_text("📅 *שבוע?*", parse_mode="Markdown",
                                       reply_markup=week_keyboard())
        return True

    if action.startswith('camp_week_'):
        week = action[len('camp_week_'):]
        ss = sheets_sessions.get(user_id, {})
        name   = ss.get('name', '')
        grade  = ss.get('grade', '')
        branch = ss.get('branch', '')
        sheets_sessions.pop(user_id, None)
        try:
            camp.add_student(name, grade, branch, week)
            await query.edit_message_text(
                f"✅ *{name}* נוסף — כיתה {grade} | {branch} | {week}",
                parse_mode="Markdown",
                reply_markup=camp_menu_keyboard(),
            )
        except Exception as e:
            await query.edit_message_text(f"❌ שגיאה: {e}", reply_markup=camp_menu_keyboard())
        return True

    # ── Camp update field pick ──
    if action.startswith('camp_field_'):
        field = action[len('camp_field_'):]
        ss = sheets_sessions.get(user_id, {})
        ss['field'] = field
        target = ss.get('target', '')

        if field == 'paid':
            ss['step'] = 'camp_upd_done_btn'
            sheets_sessions[user_id] = ss
            await query.edit_message_text(f"💳 *{target}* — שולם?", parse_mode="Markdown",
                                           reply_markup=yesno_keyboard("camp_paid_"))
        elif field == 'lunch':
            ss['step'] = 'camp_upd_done_btn'
            sheets_sessions[user_id] = ss
            await query.edit_message_text(f"🍱 *{target}* — צהרון?", parse_mode="Markdown",
                                           reply_markup=yesno_keyboard("camp_lunch_"))
        elif field == 'notes':
            ss['step'] = 'camp_upd_done_btn'
            sheets_sessions[user_id] = ss
            await query.edit_message_text(f"📅 *{target}* — שבוע?", parse_mode="Markdown",
                                           reply_markup=week_keyboard())
        elif field == 'branch':
            ss['step'] = 'camp_upd_done_btn'
            sheets_sessions[user_id] = ss
            await query.edit_message_text(f"🏠 *{target}* — סניף?", parse_mode="Markdown",
                                           reply_markup=branch_keyboard("camp_updbranch_"))
        else:
            # grade or shirt — free text
            ss['step'] = 'camp_upd_value'
            sheets_sessions[user_id] = ss
            label = {'grade': 'כיתה', 'shirt': 'מידת חולצה'}.get(field, field)
            await query.edit_message_text(f"✏️ *{label} חדשה עבור {target}?*",
                                           parse_mode="Markdown")
        return True

    if action.startswith('camp_paid_'):
        val = action[len('camp_paid_'):]
        ss = sheets_sessions.pop(user_id, {})
        camp.update_student(ss.get('target', ''), 'paid', val)
        await query.edit_message_text(f"✅ עודכן — תשלום: {val}", reply_markup=camp_menu_keyboard())
        return True

    if action.startswith('camp_lunch_'):
        val = action[len('camp_lunch_'):]
        ss = sheets_sessions.pop(user_id, {})
        camp.update_student(ss.get('target', ''), 'lunch', val)
        await query.edit_message_text(f"✅ עודכן — צהרון: {val}", reply_markup=camp_menu_keyboard())
        return True

    if action.startswith('camp_updbranch_'):
        branch = action[len('camp_updbranch_'):]
        ss = sheets_sessions.pop(user_id, {})
        camp.update_student(ss.get('target', ''), 'branch', branch)
        await query.edit_message_text(f"✅ עודכן — סניף: {branch}", reply_markup=camp_menu_keyboard())
        return True

    # camp_week used in update flow too
    if action.startswith('camp_week_') and sheets_sessions.get(user_id, {}).get('step') == 'camp_upd_done_btn':
        week = action[len('camp_week_'):]
        ss = sheets_sessions.pop(user_id, {})
        camp.update_student(ss.get('target', ''), 'notes', week)
        await query.edit_message_text(f"✅ עודכן — שבוע: {week}", reply_markup=camp_menu_keyboard())
        return True

    # ── Lyla main menu actions ──
    if action == 'lyla_list':
        text = _lyla_list_text()
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=lyla_menu_keyboard())
        return True

    if action == 'lyla_stats':
        s = lyla.get_stats()
        lines = [f"🌸 *לילה יפני — סטטיסטיקה*\n", f"סה\"כ: *{s['total']}* משתתפים\n"]
        for b, n in sorted(s['by_branch'].items(), key=lambda x: -x[1]):
            lines.append(f"  {b}: {n}")
        await query.edit_message_text('\n'.join(lines), parse_mode="Markdown",
                                      reply_markup=lyla_menu_keyboard())
        return True

    if action == 'lyla_add':
        sheets_sessions[user_id] = {'step': 'lyla_add_name', 'mode': 'lyla'}
        await query.edit_message_text("➕ *שם המשתתף?*", parse_mode="Markdown")
        return True

    if action == 'lyla_format':
        await query.edit_message_text("🎨 מעצב גיליון לילה יפני...")
        try:
            lyla.format_sheet()
            await query.edit_message_text("✅ גיליון לילה יפני עוצב מחדש.",
                                           reply_markup=lyla_menu_keyboard())
        except Exception as e:
            await query.edit_message_text(f"❌ שגיאה: {e}", reply_markup=lyla_menu_keyboard())
        return True

    if action.startswith('lyla_branch_'):
        branch = action[len('lyla_branch_'):]
        ss = sheets_sessions.pop(user_id, {})
        name  = ss.get('name', '')
        grade = ss.get('grade', '')
        try:
            added = lyla.add_student_direct(name, grade, branch)
            if added:
                await query.edit_message_text(
                    f"✅ *{name}* נוסף — כיתה {grade} | {branch}",
                    parse_mode="Markdown",
                    reply_markup=lyla_menu_keyboard(),
                )
            else:
                await query.edit_message_text(
                    f"⚠️ *{name}* כבר קיים ברשימה.",
                    parse_mode="Markdown",
                    reply_markup=lyla_menu_keyboard(),
                )
        except Exception as e:
            await query.edit_message_text(f"❌ שגיאה: {e}", reply_markup=lyla_menu_keyboard())
        return True

    return False


def record_action(user_id: str, action_type: str, description: str, undo_data: dict):
    """Record an action for potential undo."""
    history = action_history.setdefault(user_id, [])
    history.append({
        "type":        action_type,
        "description": description,
        "undo_data":   undo_data,
        "timestamp":   datetime.now(IL_TZ).isoformat(),
    })
    # Keep last 10 actions
    action_history[user_id] = history[-10:]


def undo_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("↩️ בטל פעולה אחרונה", callback_data="undo_last")
    ]])


def payment_approval_buttons(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ אשר ועדכן בדוח", callback_data=f"pay_approve|{key}"),
            InlineKeyboardButton("❌ דחה", callback_data=f"pay_reject|{key}"),
        ],
        [InlineKeyboardButton("✏️ שנה סכום/חודש", callback_data=f"pay_edit|{key}")],
    ])






async def wa_payment_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """
    כל ראשון בשבוע בשעה 09:00 — שולח לטופז סיכום חייבים עם כפתור שליחה.
    """
    from datetime import date
    if not TOPAZ_CHAT_ID:
        return
    try:
        month = date.today().strftime("%m/%Y")
        unpaid = payments_report.get_unpaid(month)
        if not unpaid:
            return  # Everyone paid — no reminder needed

        lines = [f"💰 *תזכורת שבועית — חייבים לחודש {month}*\n"]
        for s in unpaid[:15]:
            lines.append(f"• {s['name']} — {s.get('amount', '')}₪")
        if len(unpaid) > 15:
            lines.append(f"_...ועוד {len(unpaid)-15} נוספים_")

        lines.append("\n📱 לשליחת תזכורות WhatsApp: /unpaid")
        await context.bot.send_message(
            chat_id=TOPAZ_CHAT_ID,
            text="\n".join(lines),
            parse_mode="Markdown"
        )
    except Exception as e:
        log.error(f"wa_payment_reminder_job error: {e}")

async def on_startup(app):
    """Notify Topaz when bot comes online + auto-reconnect WhatsApp in background."""
    import threading as _threading
    # Auto-start WA service in background (non-blocking)
    # Auth files persist on /data disk, so no QR needed after restart
    def _bg_wa():
        try:
            wa_client.start_service()
        except Exception as e:
            log.warning("WA auto-start failed: %s", e)
    _threading.Thread(target=_bg_wa, daemon=True).start()

    if TOPAZ_CHAT_ID:
        from datetime import datetime as _dt
        now = _dt.now(IL_TZ).strftime("%d/%m/%Y %H:%M")
        try:
            await app.bot.send_message(
                chat_id=TOPAZ_CHAT_ID,
                text=f"✅ *הבוט עלה לאוויר* — {now}\n\nכל המערכות פעילות 🟢",
                parse_mode="Markdown"
            )
        except Exception:
            pass


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler — logs and notifies Topaz on every unhandled exception."""
    import traceback
    tb = "".join(traceback.format_exception(None, context.error, context.error.__traceback__))
    log.error("Unhandled exception:\n%s", tb)
    if TOPAZ_CHAT_ID:
        short = str(context.error)[:300]
        user_info = ""
        if isinstance(update, Update) and update.effective_user:
            user_info = f" (מ-{update.effective_user.first_name})"
        try:
            await context.bot.send_message(
                chat_id=TOPAZ_CHAT_ID,
                text=f"⚠️ *שגיאה בבוט{user_info}:*\n`{short}`",
                parse_mode="Markdown"
            )
        except Exception:
            pass


async def registration_sync_job(context):
    """Background job — runs every hour. Checks for new event registrations + cleans stale sessions."""
    if not TOPAZ_CHAT_ID:
        return
    # Clean stale in-memory sessions (prevent memory leak)
    import time as _time
    now_ts = _time.time()
    for d in [sheets_sessions, pending_belt_events, calendar_sessions, payment_sync_sessions]:
        stale = [k for k, v in d.items()
                 if isinstance(v, dict) and now_ts - v.get("_ts", now_ts) > 3600]
        for k in stale:
            d.pop(k, None)
    try:
        report = registration_sync.run_sync_and_report()
        if report:
            await context.bot.send_message(
                chat_id=TOPAZ_CHAT_ID,
                text=report,
                parse_mode="Markdown"
            )
    except Exception as e:
        log.error(f"registration_sync_job error: {e}")


async def email_monitor_job(context):
    """Background job — runs every 10 min. Checks Gmail for new payment emails."""
    if not TOPAZ_CHAT_ID:
        return

    emails = email_reader.fetch_new_emails()
    if not emails:
        return

    for em in emails:
        # Ask Claude if this is a payment-related email
        prompt = (
            f"מייל שהתקבל:\nנושא: {em['subject']}\nמ: {em['sender']}\nתוכן:\n{em['body'][:1500]}\n\n"
            "האם זה מייל שקשור לתשלום של ספורטאי ג'ודו? "
            "אם כן, חלץ: שם הספורטאי, חודש התשלום, סכום. "
            "השב בפורמט JSON בלבד כך:\n"
            '{\"is_payment\": true, \"student_name\": \"שם מלא\", \"month\": \"ספטמבר\", \"amount\": \"200\"}\n'
            "אם לא קשור לתשלום: {\"is_payment\": false}"
        )

        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()
            # Extract JSON
            import re as _re
            json_match = _re.search(r'\{.*\}', raw, _re.DOTALL)
            if not json_match:
                email_reader.mark_skipped(em["id"])
                continue
            data = json.loads(json_match.group())
        except Exception:
            email_reader.mark_skipped(em["id"])
            continue

        if not data.get("is_payment"):
            email_reader.mark_skipped(em["id"])
            continue

        student_name = data.get("student_name", "")
        month        = data.get("month", "")
        amount       = data.get("amount", "")

        # Try to find student in sheet
        student = payments_sheet.find_student(student_name) if student_name else None

        key = f"pay_{em['id']}"
        pending_payments[key] = {
            "email_id":     em["id"],
            "subject":      em["subject"],
            "sender":       em["sender"],
            "student_name": student_name,
            "student":      student,
            "month":        month,
            "amount":       amount,
        }

        # Build approval message
        if student:
            current = payments_sheet.get_month_value(student["row"], month)
            paid_info = payments_sheet.payment_summary_row(student)
            student_line = f"✅ נמצא: *{student['full_name']}* (שורה {student['row']}, {student['club']})"
            current_line = f"ערך נוכחי ב{month}: {current or 'ריק'}"
            paid_line    = f"תשלומים קיימים: {paid_info}"
        else:
            student_line = f"⚠️ לא נמצא ספורטאי בשם *{student_name}* — בדוק ידנית"
            current_line = ""
            paid_line    = ""

        msg_lines = [
            "💰 *תשלום חדש זוהה במייל*",
            f"",
            f"📧 נושא: {em['subject'][:60]}",
            f"👤 שולח: {em['sender'][:40]}",
            f"",
            f"📋 *מה זוהה:*",
            f"• ספורטאי: {student_name}",
            f"• חודש: {month}",
            f"• סכום: {amount}₪",
            f"",
            student_line,
        ]
        if current_line:
            msg_lines.append(current_line)
        if paid_line:
            msg_lines.append(paid_line)
        msg_lines += ["", "האם לעדכן בדוח התשלומים?"]

        await context.bot.send_message(
            chat_id=TOPAZ_CHAT_ID,
            text="\n".join(msg_lines),
            parse_mode="Markdown",
            reply_markup=payment_approval_buttons(key),
        )

        # Mark as seen so we don't process it again
        email_reader.mark_seen(em["id"])


# ═══════════════════════════════════════════════════════════════════════════
# 1. תזכורת אימון יומית
# ═══════════════════════════════════════════════════════════════════════════

async def daily_training_reminder_job(context):
    """Runs every morning at 07:00 Israel time — daily summary with calendar + training."""
    if not TOPAZ_CHAT_ID:
        return
    hour = datetime.now(IL_TZ).hour
    if hour != 7:
        return

    from datetime import date as _date
    today = _date.today()
    day_name = ws.today_name()
    date_str = today.strftime("%d/%m/%Y")
    schedule = ws.today_schedule()

    lines = [f"🌅 *בוקר טוב! יום {day_name} {date_str}*\n"]

    # ── גוגל יומן היום ──
    try:
        events = cal.get_events_range(today, today)
        if events:
            lines.append("📅 *יומן היום:*")
            for ev in events:
                time_part = f" {ev['time']}" if ev["time"] else ""
                lines.append(f"  {ev['emoji']}{time_part} {ev['title']}")
            lines.append("")
    except Exception as e:
        log.warning("daily calendar fetch error: %s", e)

    # ── לוז אימונים ──
    if schedule:
        from training_plans import SPREADSHEET_ID
        lines.append("🥋 *אימונים היום:*")
        for entry in schedule:
            branch = entry["branch"]
            lines.append(f"  📍 *{branch}*")
            for g in entry["groups"]:
                cancelled_note = " 🚫 בוטל" if g.get("cancelled") else ""
                lines.append(f"    {g['time']} — {g['name']}{cancelled_note}")
        url = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit#gid=0"
        lines.append(f"\n  🔗 [פתח תוכניות אימון]({url})")
    else:
        lines.append("🏖️ *אין אימונים היום*")

    # ── התראות היעדרות חריגה ──
    try:
        absence_log = load_json(Path("absence_log.json"), {})
        alerts = []
        for name, records in absence_log.items():
            recent = records[-4:]
            consec = sum(1 for r in reversed(recent) if r.get("absent"))
            if consec >= 3:
                # Find branch from records
                last_branch = next((r.get("branch", "") for r in reversed(records) if r.get("branch")), "")
                parents = contacts_db.find_parent(name, last_branch or None)
                phone = parents[0]["phone"] if parents else "—"
                alerts.append(f"  ⚠️ {name} ({consec} ברצף) — {phone}")
        if alerts:
            lines.append("\n⚠️ *היעדרויות ברצף — לטיפול:*")
            lines.extend(alerts[:5])
    except Exception:
        pass

    # ── ימי הולדת השבוע ──
    try:
        bdays = contacts_db.birthdays_this_week()
        if bdays:
            lines.append("\n🎂 *ימי הולדת השבוע:*")
            for b in bdays:
                lines.append(f"  🎉 {b['name']} — {b['date'].day}/{b['date'].month}")
    except Exception as e:
        log.warning("birthday fetch error: %s", e)

    # ── חייבי תשלום החודש ──
    try:
        from datetime import date as _date2
        import payments_report as _pr
        he_months = ["","ינואר","פברואר","מרץ","אפריל","מאי","יוני",
                     "יולי","אוגוסט","ספטמבר","אוקטובר","נובמבר","דצמבר"]
        cur_month = he_months[_date2.today().month]
        unpaid_map = _pr.get_unpaid(cur_month)
        unpaid = (unpaid_map or {}).get(cur_month, [])
        if unpaid:
            lines.append(f"\n💰 *לא שילמו {cur_month} ({len(unpaid)} ספורטאים):*")
            for s in unpaid[:5]:
                lines.append(f"  • {s['full_name']} ({s.get('club','')})")
            if len(unpaid) > 5:
                lines.append(f"  _...ועוד {len(unpaid)-5}_")
    except Exception as e:
        log.warning("unpaid fetch error: %s", e)

    # שלח רק אם יש תוכן מעבר לכותרת
    if len(lines) <= 2:
        return

    try:
        await context.bot.send_message(
            chat_id=TOPAZ_CHAT_ID,
            text="\n".join(lines),
            parse_mode="Markdown",
        )
    except Exception as e:
        log.error("daily_training_reminder_job error: %s", e)


# ═══════════════════════════════════════════════════════════════════════════
# 3. סיכום שבועי
# ═══════════════════════════════════════════════════════════════════════════

async def weekly_summary_job(context):
    """Runs every Sunday at 08:00 Israel time — weekly training summary."""
    if not TOPAZ_CHAT_ID:
        return
    from datetime import date as _date, timedelta as _td
    today = _date.today()
    hour = datetime.now(IL_TZ).hour
    if today.weekday() != 6 or hour != 8:
        return

    week_start = today - _td(days=7)
    lines = [f"📊 *סיכום שבוע {week_start.day}/{week_start.month} – {(today - _td(days=1)).day}/{(today - _td(days=1)).month}*\n"]

    # Count plans saved this week from archive
    records = arc._load()
    from datetime import datetime as _dt
    week_plans = [
        r for r in records
        if r.get("saved_at", "") >= week_start.isoformat()
    ]

    if week_plans:
        by_branch: dict[str, list] = {}
        for r in week_plans:
            by_branch.setdefault(r["branch"], []).append(r["group"])
        lines.append(f"🥋 *תוכניות שנשמרו:* {len(week_plans)}")
        for branch, groups in sorted(by_branch.items()):
            lines.append(f"  {branch}: {', '.join(sorted(set(groups)))}")
        lines.append("")

    # Dropout check
    try:
        at_risk = dropout_detector.find_at_risk_students(consecutive=2)
        if at_risk:
            lines.append(f"⚠️ *פספסו 2+ אימונים ברצף:* {len(at_risk)} ספורטאים")
            for s in at_risk[:5]:
                lines.append(f"  • {s['name']} ({s['branch']} / {s['group']})")
            if len(at_risk) > 5:
                lines.append(f"  ... ועוד {len(at_risk)-5}")
        else:
            lines.append("✅ אין ספורטאים בסיכון נשירה השבוע")
    except Exception as e:
        log.error("weekly_summary dropout error: %s", e)

    try:
        await context.bot.send_message(
            chat_id=TOPAZ_CHAT_ID,
            text="\n".join(lines),
            parse_mode="Markdown",
        )
    except Exception as e:
        log.error("weekly_summary_job error: %s", e)


# ═══════════════════════════════════════════════════════════════════════════
# 4. חיפוש ספורטאי (מורחב)
# ═══════════════════════════════════════════════════════════════════════════


def _search_student_everywhere(name: str) -> dict | None:
    """
    מחפש ספורטאי לפי שם בכל המקורות:
    payments_report → lyla_sheet → camp_sheet → attendance sheets.
    מחזיר dict עם כל הנתונים שנמצאו, או None.
    """
    import re as _re
    name_clean = name.strip()
    result = {
        "name": name_clean,
        "found_in": [],
        "grade": "",
        "branch": "",
        "sub_type": "",
        "payments": {},
        "lyla": False,
        "camp": False,
        "camp_week": "",
        "parent": None,
    }
    found_any = False

    # 1. payments sheet
    try:
        all_st = payments_report.load_all_students()
        name_l = name_clean.lower()
        matches = [s for s in all_st if name_l in s.get("full_name", "").lower()]
        if not matches:
            from difflib import get_close_matches
            names = [s["full_name"] for s in all_st]
            close = get_close_matches(name_clean, names, n=1, cutoff=0.6)
            matches = [s for s in all_st if s["full_name"] == close[0]] if close else []
        if matches:
            s = matches[0]
            result["name"]     = s["full_name"]
            result["grade"]    = s.get("grade", "")
            result["branch"]   = s.get("club", "")
            result["sub_type"] = s.get("sub_type", "")
            result["payments"] = s.get("payments", {})
            result["found_in"].append("תשלומים")
            found_any = True
    except Exception:
        pass

    # 2. לילה יפני
    try:
        lyla_students = lyla.get_students()
        for s in lyla_students:
            if name_clean in s["name"] or s["name"] in name_clean:
                result["lyla"]   = True
                result["grade"]  = result["grade"] or s.get("grade", "")
                result["branch"] = result["branch"] or s.get("branch", "")
                result["name"]   = result["name"] or s["name"]
                result["found_in"].append("לילה יפני 🌸")
                found_any = True
                break
    except Exception:
        pass

    # 3. מחנה קיץ
    try:
        camp_students = camp.get_students()
        for s in camp_students:
            if name_clean in s["name"] or s["name"] in name_clean:
                result["camp"]      = True
                result["camp_week"] = s.get("notes", "") or s.get("shirt", "")
                result["grade"]     = result["grade"] or s.get("grade", "")
                result["branch"]    = result["branch"] or s.get("branch", "")
                result["name"]      = result["name"] or s["name"]
                result["found_in"].append("מחנה קיץ ☀️")
                found_any = True
                break
    except Exception:
        pass

    # 4. contacts — parent lookup
    try:
        parent = contacts_db.get_parent_for_student(result["name"], result["branch"] or None)
        if parent:
            result["parent"] = parent
    except Exception:
        pass

    return result if found_any else None


def _format_student_card_full(data: dict) -> str:
    """מעצב כרטיס ספורטאי מלא מנתוני _search_student_everywhere."""
    lines = [f"👤 *{data['name']}*"]
    if data.get("branch"):
        lines.append(f"📍 סניף: {data['branch']}")
    if data.get("grade"):
        lines.append(f"🏫 כיתה: {data['grade']}")
    if data.get("sub_type"):
        lines.append(f"📋 מנוי: {data['sub_type']}")

    if data.get("found_in"):
        lines.append(f"📂 רשום ב: {', '.join(data['found_in'])}")

    if data.get("lyla"):
        lines.append("🌸 לילה יפני: ✅ רשום")
    if data.get("camp"):
        week = f" ({data['camp_week']})" if data.get("camp_week") else ""
        lines.append(f"☀️ מחנה קיץ: ✅ רשום{week}")

    if data.get("payments"):
        paid   = [f"{m}: {v}₪" for m, v in data["payments"].items() if v]
        unpaid = [m for m, v in data["payments"].items() if not v]
        if paid:
            lines.append(f"\n✅ שילם: {', '.join(paid)}")
        if unpaid:
            lines.append(f"❌ חסר תשלום: {', '.join(unpaid)}")

    if data.get("parent"):
        p = data["parent"]
        lines.append(f"\n👨‍👩‍👧 הורה: {p.get('parent_name', '—')}")
        phone = p.get("phone", "")
        if phone:
            lines.append(f"📱 טלפון: {phone}")
            wa = f"https://wa.me/972{phone.lstrip('0')}"
            lines.append(f"[📲 WhatsApp]({wa})")

    return "\n".join(lines)


async def cmd_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/student <שם> — כרטיס ספורטאי מלא מכל המקורות."""
    if not context.args:
        await update.message.reply_text("שלח: `/student שם`\nדוגמה: `/student יובל עשור`", parse_mode="Markdown")
        return
    await update.message.chat.send_action("typing")
    name = " ".join(context.args)

    data = _search_student_everywhere(name)
    if not data:
        await update.message.reply_text(f"❌ לא מצאתי ספורטאי בשם *{name}*\n\nנסה שם אחר או חלק מהשם.", parse_mode="Markdown")
        return

    card = _format_student_card_full(data)
    await send_long(update, card, parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════════════════
# 6. ארכיון תוכניות
# ═══════════════════════════════════════════════════════════════════════════

async def cmd_archive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/archive [שאילתה] — חיפוש בארכיון תוכניות האימון."""
    await update.message.chat.send_action("typing")

    if not context.args:
        # Show stats
        msg = arc.stats()
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    query = " ".join(context.args)
    results = arc.search(query)

    if not results:
        await update.message.reply_text(
            f"🔍 לא מצאתי תוכניות עבור: *{query}*\n\nנסה: שם קבוצה, סניף, או תאריך",
            parse_mode="Markdown"
        )
        return

    lines = [f"🔍 *תוצאות עבור: {query}*\n"]
    for r in results:
        lines.append(arc.format_plan(r))
        lines.append("")

    await send_long(update, "\n".join(lines), parse_mode="Markdown")


async def cmd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/edit [סניף] [תאריך] — עריכת או צפייה בתוכנית קיימת בגיליון."""
    user_id = str(update.effective_user.id)
    args = context.args or []

    branch   = None
    plan_date = None

    # Parse args: "/edit סירקין 26/6" or "/edit סירקין" or "/edit"
    if args:
        # Try to find branch
        for b in tp.BRANCH_TABS:
            for a in args:
                if b in a or a in b:
                    branch = b
                    break
            if branch:
                break
        # Try to find date
        from datetime import date as _date_cls
        import re as _re
        for a in args:
            m = _re.match(r'(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?', a)
            if m:
                day, mo = int(m.group(1)), int(m.group(2))
                yr = int(m.group(3)) if m.group(3) else _date_cls.today().year
                if yr < 100: yr += 2000
                try:
                    plan_date = _date_cls(yr, mo, day)
                except ValueError:
                    pass

    # If branch+date known → load and show current plan
    if branch and plan_date:
        await update.message.chat.send_action("typing")
        try:
            current = tp.load_plan_from_sheet(branch, plan_date)
        except Exception as e:
            current = None
        day_he = ws.day_name(plan_date)
        date_str = f"{day_he} {plan_date.day}/{plan_date.month}/{plan_date.year}"

        if current:
            lines = [f"📋 *תוכנית קיימת — {branch} | {date_str}*\n"]
            for g_name, items in current.items():
                lines.append(f"*{g_name}:*")
                for row_type, val in items.items():
                    if val:
                        lines.append(f"  {row_type}: {val}")
            lines.append(f"\n✏️ לעדכן — שלח תוכנית חדשה ואז לחץ *שמור*")
            await update.message.reply_text(
                "\n".join(lines),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        f"💾 שמור עדכון — {branch} | {plan_date.day}/{plan_date.month}",
                        callback_data=f"plan_save_quick|{branch}|{plan_date.isoformat()}"
                    )],
                    [InlineKeyboardButton("✏️ ערוך", callback_data=f"plan_edit_current")],
                ])
            )
            sheets_sessions[user_id] = {
                "step": "fd_waiting_plan",
                "branch": branch,
                "plan_date": plan_date.isoformat(),
            }
        else:
            await update.message.reply_text(
                f"📋 *{branch} | {date_str}* — אין תוכנית שמורה\n\n"
                "שלח תוכנית לשמירה:",
                parse_mode="Markdown",
                reply_markup=cancel_button()
            )
            sheets_sessions[user_id] = {
                "step": "fd_waiting_plan",
                "branch": branch,
                "plan_date": plan_date.isoformat(),
            }
        return

    # If only branch → pick date from next training dates
    if branch:
        dates = ws.next_training_dates(branch, 5)
        rows = []
        for d in dates:
            day_he = ws.day_name(d)
            label = f"{day_he} {d.day}/{d.month}"
            rows.append([InlineKeyboardButton(
                label, callback_data=f"edit_date|{branch}|{d.isoformat()}"
            )])
        rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
        await update.message.reply_text(
            f"✏️ *עריכת תוכנית — {branch}*\n\nאיזה תאריך?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows)
        )
        return

    # No args → pick branch
    branch_rows = [
        [InlineKeyboardButton(b, callback_data=f"edit_branch|{b}")]
        for b in tp.BRANCH_TABS
    ]
    branch_rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
    await update.message.reply_text(
        "✏️ *עריכת תוכנית — איזה סניף?*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(branch_rows)
    )


# ═══════════════════════════════════════════════════════════════════════════
# Invoice4u payment sync — callbacks + text handler
# ═══════════════════════════════════════════════════════════════════════════

async def handle_inv4u_callback(query, user_id: str, action: str, context):
    """Handle all inv4u_* callbacks for the payment sync flow."""
    await query.answer()
    ss = payment_sync_sessions.get(user_id, {})

    # ── Month selection from multi-month file ──
    if action.startswith("inv4u_month|"):
        month_key = action.split("|", 1)[1]
        records   = ss.get("records", [])
        payment_sync_sessions.pop(user_id, None)
        await _inv4u_start_month(query, context, user_id, records, month_key)
        return

    # ── Start review (begin handling unknowns or go straight to summary) ──
    if action == "inv4u_start_review":
        unknowns = ss.get("unknowns", [])
        if unknowns:
            ss["current_unknown"] = 0
            ss["step"] = "unknown_review"
            payment_sync_sessions[user_id] = ss
            text, markup = _inv4u_unknown_prompt(ss)
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        else:
            await _inv4u_show_summary(query, user_id)
        return

    # ── Skip current unknown ──
    if action == "inv4u_unknown_skip":
        idx = ss.get("current_unknown", 0)
        unknowns = ss.get("unknowns", [])
        if idx < len(unknowns):
            unknowns[idx]["status"] = "skipped"
        idx += 1
        ss["current_unknown"] = idx
        payment_sync_sessions[user_id] = ss
        if idx < len(unknowns):
            text, markup = _inv4u_unknown_prompt(ss)
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        else:
            await query.edit_message_text("✅ סיום זיהוי — מכין סיכום...")
            await _inv4u_show_summary(query, user_id)
        return

    # ── User picked a student for unknown ──
    if action.startswith("inv4u_pick_student|"):
        parts   = action.split("|")
        s_first = parts[1] if len(parts) > 1 else ""
        s_last  = parts[2] if len(parts) > 2 else ""
        s_branch = parts[3] if len(parts) > 3 else ""

        # Find student in sheet_students
        sheet_students = ss.get("sheet_students", [])
        student = next(
            (s for s in sheet_students
             if s["first"] == s_first and s["last"] == s_last
             and s.get("branch") == s_branch),
            None
        )
        if not student:
            student = {"first": s_first, "last": s_last, "branch": s_branch,
                       "sheet": "", "row_idx": 0}

        # Save in mapping
        idx      = ss.get("current_unknown", 0)
        unknowns = ss.get("unknowns", [])
        if idx < len(unknowns):
            item = unknowns[idx]
            rec  = item["record"]
            mapping = ss.get("mapping", {})
            mapping = payment_matcher.add_to_mapping(
                rec.get("customer_id", ""), rec["customer_name"], student, mapping
            )
            ss["mapping"] = mapping
            unknowns[idx]["status"]  = "confirmed"
            unknowns[idx]["student"] = student

            # Also update matched_monthly entry
            matched_monthly = ss.get("matched_monthly", [])
            for m in matched_monthly:
                if m.get("mapping_key") == item.get("mapping_key"):
                    m["status"]  = "confirmed"
                    m["student"] = student
                    break

            idx += 1
            ss["current_unknown"] = idx
            ss["unknowns"]        = unknowns
            payment_sync_sessions[user_id] = ss

        if idx < len(unknowns):
            text, markup = _inv4u_unknown_prompt(ss)
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        else:
            await query.edit_message_text("✅ כל התשלומים זוהו — מכין סיכום...")
            await _inv4u_show_summary(query, user_id)
        return

    # ── Final confirm — write to sheets ──
    if action == "inv4u_confirm_write":
        await query.edit_message_text("⏳ כותב לגיליון...")
        to_write = ss.get("to_write", [])
        matched_belts = ss.get("matched_belts", [])
        month_he = ss.get("month", "")

        written    = []
        belt_lines = []
        errors     = []

        # Write monthly payments in batch
        batch_items = []
        for m in to_write:
            s = m.get("student")
            if not s or not s.get("sheet"):
                continue
            batch_items.append({
                "student": s,
                "month":   month_he,
                "amount":  m["record"]["amount"],
            })

        if batch_items:
            try:
                results = invoice4u_sync.write_monthly_batch(batch_items)
                written = batch_items
            except Exception as e:
                errors.append(f"תשלומים חודשיים: {e}")

        # Write belt payments — skip unmatched records (student=None) to avoid blank-branch rows
        for m in matched_belts:
            if not m.get("student"):
                errors.append(f"חגורה לא זוהתה: {m['record'].get('customer_name','?')} — דלג")
                continue
            rec = m["record"]
            parent = rec.get("parent_name", rec["customer_name"])
            children = rec.get("children", [])
            first_name = children[0] if children else parent.split()[0]
            last_name  = parent.split()[-1] if len(parent.split()) > 1 else ""
            branch     = m["student"]["branch"]
            try:
                line = invoice4u_sync.write_belt_payment(
                    first_name, last_name, branch, rec["date"]
                )
                belt_lines.append(line)
            except Exception as e:
                errors.append(f"חגורה {first_name}: {e}")

        unknowns_left = sum(
            1 for m in ss.get("matched_monthly", [])
            if m["status"] in ("unknown", "skipped")
        )

        summary = invoice4u_sync.format_sync_summary(
            written, belt_lines, [], [], unknowns_left
        )
        if errors:
            summary += "\n\n⚠️ שגיאות:\n" + "\n".join(f"• {e}" for e in errors)

        payment_sync_sessions.pop(user_id, None)
        await query.edit_message_text(summary, parse_mode="Markdown")
        return


async def handle_inv4u_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text input during invoice4u unknown-name flow. Returns True if consumed."""
    user_id = str(update.effective_user.id)
    ss = payment_sync_sessions.get(user_id)
    if not ss or ss.get("step") != "unknown_review":
        return False

    text = update.message.text.strip()
    if text.lower() in ("דלג", "skip", "ביטול", "cancel"):
        # Treat as skip
        idx = ss.get("current_unknown", 0)
        unknowns = ss.get("unknowns", [])
        if idx < len(unknowns):
            unknowns[idx]["status"] = "skipped"
            idx += 1
            ss["current_unknown"] = idx
            payment_sync_sessions[user_id] = ss
        if idx < len(unknowns):
            text_msg, markup = _inv4u_unknown_prompt(ss)
            await update.message.reply_text(text_msg, parse_mode="Markdown", reply_markup=markup)
        else:
            await _inv4u_show_summary(update, user_id)
        return True

    # Search for student
    sheet_students = ss.get("sheet_students", [])
    results = payment_matcher.search_student(text, sheet_students)
    if not results:
        await update.message.reply_text("❌ לא מצאתי — נסה שם אחר, או לחץ ⏭ לדלג")
        return True

    buttons = []
    for s in results[:6]:
        label = f"{s['first']} {s['last']} ({s.get('branch', '')})"
        data  = f"inv4u_pick_student|{s['first']}|{s['last']}|{s.get('branch', '')}"
        buttons.append([InlineKeyboardButton(label, callback_data=data)])
    buttons.append([InlineKeyboardButton("⏭ דלג", callback_data="inv4u_unknown_skip")])

    await update.message.reply_text(
        f"🔍 תוצאות עבור *{text}*:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return True






async def cmd_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/message [שם] [סוג] — הודעת WhatsApp מוכנה להורה.
    סוגים: חיסור | תשלום | חגורה | כללי (ברירת מחדל: כללי)
    דוגמה: /message נועם כהן תשלום
    """
    if not context.args:
        await update.message.reply_text(
            "📲 *שליחת הודעה להורה*\n\n"
            "שימוש: `/message [שם] [סוג]`\n"
            "סוגים: חיסור | תשלום | חגורה | כללי\n\n"
            "דוגמה: `/message נועם כהן תשלום`",
            parse_mode="Markdown"
        )
        return

    await update.message.chat.send_action("typing")

    # זיהוי סוג ההודעה (המילה האחרונה אם היא סוג ידוע)
    MSG_TYPES = {"חיסור", "תשלום", "חגורה", "כללי"}
    args = list(context.args)
    msg_type = "כללי"
    if args and args[-1] in MSG_TYPES:
        msg_type = args.pop()
    name = " ".join(args).strip()

    if not name:
        await update.message.reply_text("❌ יש לציין שם ספורטאי")
        return

    # חיפוש הורה
    parent = contacts_db.get_parent_for_student(name)
    if not parent:
        # נסה עם חיפוש חלקי
        all_students = payments_report.load_all_students()
        name_lower = name.lower()
        match = next((s for s in all_students if name_lower in s.get("full_name","").lower()), None)
        if match:
            parent = contacts_db.get_parent_for_student(match["full_name"], match.get("club"))
            name = match["full_name"]

    if not parent:
        await update.message.reply_text(f"⚠️ לא מצאתי הורה עבור *{name}*\nבדוק שהשם תואם לגיליון.", parse_mode="Markdown")
        return

    parent_name = parent.get("parent_name", "הורה")
    phone = parent.get("phone", "")
    first_name = name.split()[0] if name else name

    # בניית ההודעה לפי סוג
    if msg_type == "חיסור":
        msg = (
            f"שלום {parent_name},\n"
            f"שמתי לב ש{first_name} לא הגיע/ה לאימון האחרון.\n"
            f"הכל בסדר? אנחנו כאן אם יש משהו.\n"
            f"נשמח לראות אותו/ה בפעם הבאה 🥋"
        )
    elif msg_type == "תשלום":
        msg = (
            f"שלום {parent_name},\n"
            f"נשמח אם תוכל/י לסדר את תשלום דמי החבר עבור {first_name}.\n"
            f"ניתן לשלם דרך הקישור או בהעברה בנקאית.\n"
            f"תודה רבה 🙏"
        )
    elif msg_type == "חגורה":
        msg = (
            f"שלום {parent_name},\n"
            f"שמחים לבשר ש{first_name} עבר/ה בהצלחה מבחן חגורה! 🎉\n"
            f"כל הכבוד על ההתמדה והעבודה הקשה.\n"
            f"נמשיך לעבוד יחד 💪"
        )
    else:  # כללי
        msg = (
            f"שלום {parent_name},\n"
            f"כאן טופז ממועדון Wolves Judo.\n"
            f"רציתי ליצור קשר בנוגע ל{first_name}.\n"
            f"אשמח אם תחזור/י אלי בהזדמנות, תודה!"
        )

    wa_number = "972" + phone.lstrip("0") if phone else ""
    import urllib.parse
    wa_url = f"https://wa.me/{wa_number}?text={urllib.parse.quote(msg)}" if wa_number else ""

    # If WhatsApp is connected — send with approval button
    # Otherwise fall back to wa.me link
    if wa_number and wa_client.is_connected():
        await wa_send_with_approval(
            context,
            chat_id=str(update.effective_chat.id),
            phone=wa_number,
            recipient_name=f"{parent_name} (הורה של {first_name})",
            message=msg
        )
    else:
        reply = (
            f"📲 *הודעה ל{parent_name}* ({phone})\n"
            f"ספורטאי: *{name}*\n\n"
            f"```\n{msg}\n```\n\n"
        )
        if wa_url:
            reply += f"[🟢 פתח ב-WhatsApp]({wa_url})"
        if not wa_client.is_connected():
            reply += "\n\n_💡 חבר WhatsApp עם /wa\_connect לשליחה אוטומטית_"
        await update.message.reply_text(reply, parse_mode="Markdown", disable_web_page_preview=True)

async def cmd_activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/activate [סניף] [שם] — החזרת ספורטאי לפעיל."""
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ שימוש: /activate [סניף] [שם]\n"
            "לדוגמה: /activate סירקין נועם כהן"
        )
        return
    branch = context.args[0]
    if branch not in att.BRANCH_SHEETS:
        await update.message.reply_text(f"❌ סניף לא מוכר: {branch}\nסניפים: {', '.join(att.BRANCH_SHEETS.keys())}")
        return
    student_name = " ".join(context.args[1:])
    msg = await update.message.reply_text(f"⏳ מחפש {student_name}...")
    try:
        result = att.activate_student(branch, student_name)
        await msg.edit_text(result)
    except Exception as e:
        await msg.edit_text(f"❌ שגיאה: {e}")

async def cmd_deactivate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/deactivate [סניף] [שם] — סימון ספורטאי כלא פעיל בגיליון."""
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ שימוש: /deactivate [סניף] [שם]\n"
            "לדוגמה: /deactivate סירקין נועם כהן\n\n"
            f"סניפים: {', '.join(att.BRANCH_SHEETS.keys())}"
        )
        return

    branch = context.args[0]
    if branch not in att.BRANCH_SHEETS:
        await update.message.reply_text(
            f"❌ סניף לא מוכר: {branch}\n"
            f"סניפים: {', '.join(att.BRANCH_SHEETS.keys())}"
        )
        return

    student_name = " ".join(context.args[1:])
    msg = await update.message.reply_text(f"⏳ מחפש {student_name} בסניף {branch}...")
    try:
        result = att.deactivate_student(branch, student_name)
        await msg.edit_text(result)
    except Exception as e:
        await msg.edit_text(f"❌ שגיאה: {e}")



async def cmd_registrations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/registrations [שם אירוע] — רשימת נרשמים ומשלמים לאירוע מהמיילים."""
    if not context.args:
        await update.message.reply_text(
            "🎫 *הרשמות לאירוע*\n\n"
            "שימוש: `/registrations [שם]`\n"
            "דוגמאות:\n"
            "  `/registrations לילה יפני`\n"
            "  `/registrations מחנה קיץ`\n"
            "  `/registrations מחנה אימונים`",
            parse_mode="Markdown"
        )
        return

    keyword = " ".join(context.args)
    msg = await update.message.reply_text(f"🔍 מחפש הרשמות עבור: *{keyword}*...", parse_mode="Markdown")
    await update.message.chat.send_action("typing")

    try:
        import email_reader as er
        registrations = er.search_event_registrations(keyword)
    except Exception as e:
        await msg.edit_text(f"❌ שגיאה: {e}")
        return

    if not registrations:
        await msg.edit_text(
            f"⚠️ לא נמצאו הרשמות עבור: *{keyword}*\n"
            f"ודא שמילת החיפוש תואמת לשם האירוע במייל.",
            parse_mode="Markdown"
        )
        return

    # Group by event name
    by_event = {}
    for r in registrations:
        by_event.setdefault(r["event_name"], []).append(r)

    lines = [f"🎫 *הרשמות: {keyword}*\n"]
    total_amount = 0

    for event_name, participants in by_event.items():
        lines.append(f"📋 *{event_name}* ({len(participants)} משתתפים):")
        for i, p in enumerate(participants, 1):
            phone_str = f" — {p['phone']}" if p['phone'] else ""
            price_str = f" ₪{p['price']}" if p['price'] else ""
            lines.append(f"  {i}. {p['name']}{phone_str}{price_str}")
            try:
                total_amount += float(p['price'].replace(',','')) if p['price'] else 0
            except Exception:
                pass
        lines.append("")

    if total_amount:
        lines.append(f"💰 *סה״כ שולם: ₪{total_amount:,.0f}*")

    await msg.edit_text("\n".join(lines), parse_mode="Markdown")

async def cmd_week_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/week_plan [סניף] — כל תוכניות האימון של הסניף לשבוע הקרוב."""
    from datetime import date as _date, timedelta as _td

    if not context.args:
        branches = ", ".join(tp.BRANCH_TABS.keys())
        await update.message.reply_text(
            f"📅 שימוש: `/week_plan [סניף]`\nסניפים: {branches}",
            parse_mode="Markdown"
        )
        return

    branch = context.args[0]
    if branch not in tp.BRANCH_TABS:
        await update.message.reply_text(
            f"❌ סניף לא מוכר: {branch}\nסניפים: {', '.join(tp.BRANCH_TABS.keys())}"
        )
        return

    await update.message.chat.send_action("typing")
    today = _date.today()
    lines = [f"📅 *תוכניות אימון — {branch} — שבוע {today.day}/{today.month}*\n"]

    found_any = False
    for i in range(7):
        d = today + _td(days=i)
        try:
            plan = tp.load_plan_from_sheet(branch, d)
        except Exception:
            continue
        if not plan:
            continue
        found_any = True
        day_names = ["שני","שלישי","רביעי","חמישי","שישי","שבת","ראשון"]
        day_name = day_names[d.weekday()]
        lines.append(f"📆 *{day_name} {d.day}/{d.month}:*")
        for group, items in plan.items():
            lines.append(f"  🥋 *{group}*")
            for row_type, val in items.items():
                lines.append(f"    • {row_type}: {val[:60]}{'...' if len(val)>60 else ''}")
        lines.append("")

    if not found_any:
        lines.append("⚠️ לא נמצאו תוכניות לשבוע הקרוב.")

    await send_long(update, "\n".join(lines), parse_mode="Markdown")



async def cmd_update_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/update_student [שם] [שדה]=[ערך]
    שדות אפשריים: סניף, כיתה, הערות
    לדוגמה: /update_student יובל עשור סניף=חגור
    """
    args_raw = " ".join(context.args or [])
    # split on field=value at the end
    import re as _re
    m = _re.match(r"^(.+?)\s+(סניף|כיתה|הערות|חולצה)=(.+)$", args_raw)
    if not m:
        await update.message.reply_text(
            "❌ שימוש:\n/update_student [שם] [שדה]=[ערך]\n\n"
            "שדות: סניף, כיתה, הערות, חולצה\n"
            "לדוגמה: /update_student יובל עשור סניף=חגור"
        )
        return

    name      = m.group(1).strip()
    field_heb = m.group(2).strip()
    value     = m.group(3).strip()

    field_map = {"סניף": "branch", "כיתה": "grade", "הערות": "notes", "חולצה": "shirt"}
    field_eng = field_map[field_heb]

    updated_in = []
    errors     = []

    # Try לילה יפני
    try:
        if lyla.update_student(name, field_eng, value):
            updated_in.append("לילה יפני 🌸")
    except Exception as e:
        errors.append(f"לילה יפני: {e}")

    # Try מחנה קיץ
    try:
        if camp.update_student(name, field_eng, value):
            updated_in.append("מחנה קיץ ☀️")
    except Exception as e:
        errors.append(f"מחנה קיץ: {e}")

    if updated_in:
        sheets_str = " + ".join(updated_in)
        await update.message.reply_text(
            f"✅ עודכן: *{name}*\n{field_heb} → {value}\n\n📋 גיליונות: {sheets_str}",
            parse_mode="Markdown"
        )
    else:
        msg = f"⚠️ לא נמצא *{name}* באף גיליון."
        if errors:
            msg += "\n" + "\n".join(errors)
        await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help — רשימת כל הפקודות."""
    text = """🥋 *Wolves Judo Bot — פקודות*

📅 *יומן*
/today /tomorrow /week /month

👥 *ספורטאים*
/student [שם] — כרטיס ספורטאי
/update\_student [שם] [שדה]=[ערך]
/dropout — נעדרים 3+ אימונים

💰 *תשלומים*
/unpaid [חודש] — חייבים
/report — דו״ח כספי
/payments — סריקת מיילים

📊 *ניהול*
/stats — סטטיסטיקות
/camp — מחנה קיץ
/lyla — לילה יפני
/archive [שאילתה]

📱 *WhatsApp*
/wa\_connect — חיבור QR
/wa\_status — מצב חיבור
/wa\_groups — שליחה לקבוצה
/message [שם] — הודעה להורה

🔄 *לוג שיחות*
/conv\_log — קישור ללוג
/migrate\_history — ייצוא שיחות ישנות

📌 *כללי*
/add\_missing — הוסף ספורטאים חסרים
/myid — Chat ID שלך
"נוכחות [סניף]" — רישום נוכחות"""
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_wa_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/wa_groups [חיפוש] — מציג קבוצות WhatsApp ומאפשר שליחה."""
    if str(update.effective_user.id) != TOPAZ_CHAT_ID:
        return
    if not wa_client.is_connected():
        await update.message.reply_text("❌ WhatsApp לא מחובר — שלח /wa_connect")
        return

    keyword = " ".join(context.args or []).strip().lower()

    groups = wa_client.get_groups()
    if not groups:
        await update.message.reply_text("❌ לא נמצאו קבוצות או שגיאה בחיבור")
        return

    # Store all groups in context
    context.bot_data["wa_groups"] = {g["id"]: g for g in groups}
    favs = _load_wa_favs()

    # Separate into favorites and wolves-related
    fav_list, wolves_list = [], []
    for g in groups:
        name = g.get("name", "").strip()
        if not name:
            continue
        gid = g["id"]
        name_lower = name.lower()
        if keyword:
            if keyword in name_lower and gid not in favs:
                wolves_list.append(g)
        else:
            if gid in favs:
                fav_list.append(g)
            elif any(kw in name_lower for kw in WOLVES_KEYWORDS):
                wolves_list.append(g)

    sections = []
    if fav_list:
        sections.append(("⭐ מועדפים", fav_list))
    if wolves_list:
        title = "🥋 קבוצות וולבס ג'ודו" if not keyword else f"🔍 תוצאות: {keyword}"
        sections.append((title, wolves_list))

    if not sections:
        # Fallback: show first 20 named groups
        named = [g for g in groups if g.get("name", "").strip()][:20]
        sections.append(("📱 קבוצות", named))

    buttons = []
    for section_title, section_groups in sections:
        buttons.append([InlineKeyboardButton(f"── {section_title} ──", callback_data="noop")])
        for g in section_groups[:15]:
            name = g["name"][:35]
            star_icon = "⭐ " if g["id"] in favs else ""
            buttons.append([
                InlineKeyboardButton(f"{star_icon}{name}", callback_data=f"wa_group_pick|{g['id']}"),
                InlineKeyboardButton("⭐", callback_data=f"wa_star|{g['id']}")
            ])

    named_count = len([g for g in groups if g.get("name", "").strip()])
    hint = "💡 לחץ ⭐ לשמירת קבוצה במועדפים\nחיפוש: /wa_groups + מילת חיפוש"
    await update.message.reply_text(
        f"📱 {named_count} קבוצות נמצאו:\n{hint}",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def cmd_wa_connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/wa_connect — מחבר WhatsApp דרך QR."""
    import io, asyncio, base64
    if str(update.effective_user.id) != TOPAZ_CHAT_ID:
        return
    if wa_client.is_connected():
        await update.message.reply_text("✅ WhatsApp כבר מחובר!")
        return

    status = wa_client.get_status()
    msg = await update.message.reply_text("⏳ מפעיל שירות WhatsApp — זה לוקח עד 2 דקות בפעם הראשונה...")

    import threading
    if status.get("status") in ("logged_out", "auth_failed"):
        # Session was logged out — force reconnect with fresh QR
        wa_client.force_reconnect()
    elif not wa_client._process_alive():
        # Service not running — start it
        threading.Thread(target=wa_client.start_service, daemon=True).start()
    else:
        # Service running but not connected — force reconnect
        wa_client.force_reconnect()

    loop = asyncio.get_event_loop()

    # Poll for QR up to 120 seconds, with status updates
    for i in range(120):
        qr = await loop.run_in_executor(None, wa_client.get_qr_base64)
        if qr:
            try:
                img_bytes = base64.b64decode(qr)
                await msg.delete()
                await update.message.reply_photo(
                    photo=io.BytesIO(img_bytes),
                    caption="📱 סרוק את ה-QR מהווטסאפ שלך:\n⋮ → *מכשירים מקושרים* → *קשר מכשיר*\n\nה-QR תקף ל-60 שניות",
                    parse_mode="Markdown"
                )
            except Exception as e:
                await update.message.reply_text(f"❌ שגיאה בשליחת QR: {e}")
            return

        connected = await loop.run_in_executor(None, wa_client.is_connected)
        if connected:
            await msg.edit_text("✅ WhatsApp מחובר!")
            return

        # Status update every 30s
        if i == 30:
            await msg.edit_text("⏳ עדיין מאתחל — רגע...")
        elif i == 60:
            await msg.edit_text("⏳ WhatsApp Bridge לוקח זמן — עוד קצת...")
        elif i == 90:
            st = await loop.run_in_executor(None, wa_client.get_status)
            await msg.edit_text(f"⏳ מצב: `{st.get('status', '?')}` — ממשיך לחכות...", parse_mode="Markdown")

        await asyncio.sleep(1)

    await msg.edit_text("❌ לא הגיע QR תוך 2 דקות\n\nנסה:\n1. /wa_connect שוב\n2. בדוק שהבוט עלה תקין ב-Render")


async def cmd_wa_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/wa_status — מצב חיבור WhatsApp."""
    if str(update.effective_user.id) != TOPAZ_CHAT_ID:
        return
    status = wa_client.get_status()
    if status.get("connected"):
        await update.message.reply_text("✅ WhatsApp מחובר ופעיל")
    else:
        st = status.get("status", "לא ידוע")
        await update.message.reply_text(
            f"🔴 WhatsApp לא מחובר\nמצב: `{st}`\n\nשלח /wa\_connect להתחברות",
            parse_mode="Markdown"
        )


async def wa_send_with_approval(
    context,
    chat_id: str,
    phone: str,
    recipient_name: str,
    message: str
):
    """
    שולח הודעת WhatsApp עם כפתור אישור בטלגרם.
    בעת אישור: שולח את ההודעה בפועל.
    """
    import json
    # Store pending message in context
    key = f"wa_{phone}_{int(__import__('time').time())}"
    context.bot_data[key] = {"phone": phone, "message": message}

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ שלח", callback_data=f"wa_send|{key}"),
            InlineKeyboardButton("❌ בטל", callback_data=f"wa_cancel|{key}"),
        ]
    ])
    preview = (
        f"📤 *שולח ל: {recipient_name}* ({phone})\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{message}\n"
        f"━━━━━━━━━━━━━━━━"
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=preview,
        parse_mode="Markdown",
        reply_markup=markup
    )



async def cmd_add_missing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/add_missing — מוסיף את הסטודנטים החסרים שזוהו ממיילי ההרשמה."""
    await update.message.reply_text("⏳ מוסיף סטודנטים חסרים...")
    results = []

    # לילה יפני
    lyla_to_add = [
        ("יובל עשור", "ח", "חגור"),
    ]
    for name, grade, branch in lyla_to_add:
        try:
            added = lyla.add_student_direct(name, grade, branch)
            results.append(f"{'✔' if added else '⚠ כבר קיים'} {name} — לילה יפני ({grade}, {branch})")
        except Exception as e:
            results.append(f"❌ {name} — שגיאה: {e}")

    # מחנה קיץ
    camp_to_add = [
        ("איתן כהן",  "ח", "סירקין", "שבוע ראשון"),
        ("עידו כהן",  "ג", "סירקין", "שבועיים"),
        ("זיו אהרוני", "", "סירקין", "שבועיים"),
    ]
    try:
        existing_camp = {s["name"] for s in camp.get_students()}
    except Exception:
        existing_camp = set()

    for name, grade, branch, week in camp_to_add:
        if name in existing_camp:
            results.append(f"⚠ כבר קיים: {name} — מחנה קיץ")
            continue
        try:
            camp.add_student(name, grade, branch, week)
            results.append(f"✔ {name} — מחנה קיץ ({grade or '?'}, {week})")
        except Exception as e:
            results.append(f"❌ {name} — שגיאה: {e}")

    await update.message.reply_text("\n".join(results) if results else "לא נמצא כלום להוסיף")



async def cmd_conv_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """מחזיר קישור לגיליון לוג השיחות — לשימוש Cowork."""
    if str(update.effective_user.id) != TOPAZ_CHAT_ID:
        return
    try:
        url = conversation_log.get_sheet_url()
        recent = conversation_log.get_recent(5)
        lines = [f"📋 *לוג שיחות בוט*", f"[פתח גיליון]({url})", ""]
        if recent:
            lines.append("*5 שיחות אחרונות:*")
            for r in recent[-5:]:
                lines.append(f"• `{r['time']}` {r['user_msg'][:60]}…")
        else:
            lines.append("_אין שיחות עדיין_")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {e}")



async def cmd_migrate_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    ממיר את היסטוריית השיחות הקיימת (conversation_history.json) לגיליון הלוג.
    רץ פעם אחת בלבד — מסמן כל שורה כ-[מיגרציה].
    """
    if str(update.effective_user.id) != TOPAZ_CHAT_ID:
        return
    await update.message.reply_text("⏳ מייצא היסטוריה קיימת לגיליון...")
    try:
        all_history = load_json(HISTORY_FILE, {})
        total = 0
        for user_id, msgs in all_history.items():
            i = 0
            while i < len(msgs) - 1:
                if msgs[i]["role"] == "user" and msgs[i+1]["role"] == "assistant":
                    user_msg  = msgs[i]["content"]
                    bot_reply = msgs[i+1]["content"]
                    # Skip system/context injections
                    if user_msg.startswith("[נתונים]"):
                        user_msg = user_msg.split("\n\n", 1)[-1]
                    action = "תוכנית אימון" if any(k in bot_reply for k in ("חימום:", "תרגול:")) else "שיחה ישנה"
                    conversation_log.log_conversation(
                        user_msg[:500],
                        bot_reply[:1000],
                        action=action,
                        notes="[מיגרציה]"
                    )
                    total += 1
                    i += 2
                else:
                    i += 1
        url = conversation_log.get_sheet_url()
        await update.message.reply_text(
            f"✅ יוצאו *{total}* שיחות לגיליון\n[פתח לוג]({url})",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {e}")


async def cmd_delete_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/delete_plan [סניף] [תאריך] — מחיקת תוכנית מהגיליון."""
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ שימוש: /delete_plan [סניף] [תאריך]\n"
            "לדוגמה: /delete_plan נבחרת 3/7/2026"
        )
        return
    branch = context.args[0]
    date_str = context.args[1]
    if branch not in tp.BRANCH_TABS:
        await update.message.reply_text(
            f"❌ סניף לא מוכר: {branch}\n"
            f"סניפים: {', '.join(tp.BRANCH_TABS.keys())}"
        )
        return
    import re as _re
    from datetime import date as _date
    m = _re.match(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", date_str)
    if not m:
        await update.message.reply_text(f"❌ תאריך לא תקין: {date_str}\nפורמט: יום/חודש או יום/חודש/שנה")
        return
    day, month = int(m.group(1)), int(m.group(2))
    year = int(m.group(3)) if m.group(3) else _date.today().year
    if year < 100:
        year += 2000
    try:
        plan_date = _date(year, month, day)
    except ValueError:
        await update.message.reply_text(f"❌ תאריך לא חוקי: {date_str}")
        return
    msg = await update.message.reply_text(f"🗑️ מוחק תוכנית {branch} {day}/{month}...")
    try:
        result = tp.clear_plan_from_sheet(branch, plan_date)
        await msg.edit_text(result)
    except Exception as e:
        await msg.edit_text(f"❌ שגיאה: {e}")


def main():
    # Clear undo file on startup — prevents stale undo buttons from prior run
    att.clear_dropout_undo()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("cleanup", cleanup_command))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("dropouts", dropouts_command))
    app.add_handler(CommandHandler("design", design_command))
    app.add_handler(CommandHandler("camp", camp_command))
    app.add_handler(CommandHandler("lyla", lyla_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("tomorrow", tomorrow_command))
    app.add_handler(CommandHandler("week", week_command))
    app.add_handler(CommandHandler("month", month_command))
    app.add_handler(CommandHandler("correction", correction_command))
    app.add_handler(CommandHandler("corrections", show_corrections_command))
    app.add_handler(CommandHandler("email", cmd_email))
    app.add_handler(CommandHandler("payments", cmd_payments))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("unpaid", cmd_unpaid))
    app.add_handler(CommandHandler("student", cmd_student))
    app.add_handler(CommandHandler("archive", cmd_archive))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("dropout", cmd_dropout))
    app.add_handler(CommandHandler("contacts", contacts_command))
    app.add_handler(CommandHandler("edit", cmd_edit))
    app.add_handler(CommandHandler("delete_plan", cmd_delete_plan))
    app.add_handler(CommandHandler("deactivate", cmd_deactivate))
    app.add_handler(CommandHandler("activate", cmd_activate))
    app.add_handler(CommandHandler("message", cmd_message))
    app.add_handler(CommandHandler("week_plan", cmd_week_plan))
    app.add_handler(CommandHandler("registrations", cmd_registrations))
    app.add_handler(CommandHandler("add_missing", cmd_add_missing))
    app.add_handler(CommandHandler("conv_log", cmd_conv_log))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("wa_connect", cmd_wa_connect))
    app.add_handler(CommandHandler("wa_status", cmd_wa_status))
    app.add_handler(CommandHandler("wa_groups", cmd_wa_groups))
    app.add_handler(CommandHandler("migrate_history", cmd_migrate_history))
    app.add_handler(CommandHandler("update_student", cmd_update_student))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    # Background jobs
    # Error handler
    app.add_error_handler(error_handler)

    # Startup notification
    app.post_init = on_startup

    if TOPAZ_CHAT_ID and app.job_queue:
        app.job_queue.run_repeating(registration_sync_job,        interval=3600,  first=90)
        # Weekly payment reminder — every Monday 09:00
        from datetime import time as _time
        app.job_queue.run_daily(
            wa_payment_reminder_job,
            time=_time(9, 0),
            days=(0,),  # Monday
        )
        app.job_queue.run_repeating(email_monitor_job,            interval=600,   first=60)
        app.job_queue.run_repeating(monthly_report_job,           interval=86400, first=120)
        app.job_queue.run_repeating(dropout_monitor_job,          interval=86400, first=180)
        app.job_queue.run_repeating(daily_training_reminder_job,  interval=86400, first=60)
        app.job_queue.run_repeating(weekly_summary_job,           interval=86400, first=120)
        log.info("Background jobs started")
    elif not app.job_queue:
        log.warning("job_queue is None — install python-telegram-bot[job-queue] to enable background jobs")
    else:
        log.warning("TOPAZ_CHAT_ID not set — background jobs disabled")

    log.info("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
