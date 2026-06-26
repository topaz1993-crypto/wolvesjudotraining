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
import training_plans as tp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = open("system_prompt.txt", encoding="utf-8").read()

# Use /data if available (Render persistent disk — survives deploys), else local
_DATA_DIR = Path("/data") if Path("/data").exists() else Path(".")
HISTORY_FILE     = _DATA_DIR / "conversation_history.json"
LOG_FILE         = _DATA_DIR / "training_log.json"
PENDING_FILE     = _DATA_DIR / "pending_plans.json"
CORRECTIONS_FILE = _DATA_DIR / "corrections.txt"


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
# sheets_sessions[user_id] = active camp/lyla flow session
sheets_sessions: dict[str, dict] = {}
# pending_belt_events[user_id] = {child_name, belt_color, ceremony_day}
pending_belt_events: dict[str, dict] = {}
# new_student_sessions[user_id] = {"session": ..., "step": "first_name"|"last_name", "first_name": "..."}
new_student_sessions: dict[str, dict] = {}
# calendar_sessions[user_id] = {"step": "pick_cal"|"pick_date"|"pick_title", "calendar": ..., "date": ..., "title": ...}
calendar_sessions: dict[str, dict] = {}


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
    "סירקין":    ["ד-ו", "ג", "א-ב", "גנים", "ז-בוגרים", "נבחרת", "איפון פייט ב-ד", "איפון פייט ה-ז"],
    "חגור":      ["ד-ח", "א-ג", "גנים"],
    "נווה ירק":  ["גנים", "ג-ז", "א-ב"],
    "אהרונוביץ": ["א-ה"],
    "פונקציונלי":["ז-ח", "ט-יב"],
    "נבחרת":     ["נבחרת"],
}

def _plan_branch_markup() -> InlineKeyboardMarkup:
    branches = ["סירקין", "חגור", "נווה ירק", "אהרונוביץ", "פונקציונלי", "נבחרת"]
    rows = []
    for i in range(0, len(branches), 2):
        rows.append([InlineKeyboardButton(b, callback_data=f"pw_branch|{b}") for b in branches[i:i+2]])
    rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
    return InlineKeyboardMarkup(rows)

def _plan_group_markup(branch: str) -> InlineKeyboardMarkup:
    groups = PLAN_GROUPS.get(branch, [])
    rows = [[InlineKeyboardButton(g, callback_data=f"pw_group|{g}")] for g in groups]
    rows.append([InlineKeyboardButton("❌ ביטול", callback_data="cancel_flow")])
    return InlineKeyboardMarkup(rows)


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
        [InlineKeyboardButton("💾 שמור בגיליון תוכניות", callback_data="save_to_sheet")],
        [InlineKeyboardButton("📋 תוכנית חדשה", callback_data="new_plan")],
    ])


async def send_long(update: Update, text: str, reply_markup=None):
    chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
    for i, chunk in enumerate(chunks):
        markup = reply_markup if i == len(chunks) - 1 else None
        await update.message.reply_text(chunk, reply_markup=markup)


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
    date_str = datetime.now().strftime("%Y-%m-%d")
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
        _hdr("─────  📅 יומן  ─────"),
        [
            InlineKeyboardButton("היום",    callback_data="menu_today"),
            InlineKeyboardButton("מחר",     callback_data="menu_tomorrow"),
            InlineKeyboardButton("השבוע",   callback_data="menu_week"),
            InlineKeyboardButton("החודש",   callback_data="menu_month"),
        ],
        [InlineKeyboardButton("➕ הוסף אירוע ליומן", callback_data="menu_cal_add")],

        # ── נוכחות ──
        _hdr("─────  ✅ נוכחות  ─────"),
        [InlineKeyboardButton("סמן נוכחות", callback_data="menu_attendance")],

        # ── אימון ──
        _hdr("─────  🥋 תוכנית אימון  ─────"),
        [
            InlineKeyboardButton("🥋 בנה תוכנית", callback_data="menu_plan"),
            InlineKeyboardButton("💾 שמור תוכנית", callback_data="menu_plan_save"),
        ],

        # ── גיליונות ──
        _hdr("─────  📂 גיליונות  ─────"),
        [
            InlineKeyboardButton("📂 פתח גיליון",     callback_data="menu_open_sheet"),
            InlineKeyboardButton("🎨 עיצוב גיליון",   callback_data="menu_design"),
        ],
        [InlineKeyboardButton("🧹 נקה עמודות ריקות",  callback_data="menu_cleanup")],

        # ── פרויקטים ──
        _hdr("─────  🏕️ פרויקטים  ─────"),
        [
            InlineKeyboardButton("🏕️ מחנה קיץ",  callback_data="menu_camp"),
            InlineKeyboardButton("🌙 לילה יפני",  callback_data="menu_lyla"),
        ],

        # ── נוסף ──
        _hdr("─────  🥇 נוסף  ─────"),
        [
            InlineKeyboardButton("🥇 חגורות",      callback_data="menu_belts"),
            InlineKeyboardButton("📊 סטטיסטיקות",  callback_data="menu_stats"),
        ],
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
    "נוכחות סירקין":     _sheet_url("1L0mcnpBPW4_3nsxaMy3EunQuOHPjWejvL1Wb6SGzltQ"),
    "נוכחות חגור":       _sheet_url("18p087VLNCRqPOhGbDzUeEg4YIHatiCfSc7v8NVFEPHA"),
    "נוכחות נווה ירק":   _sheet_url("1_J1H0q4-RGy9rH0wyhwfv-47K-uKxiHtbI-D2RoVVOU"),
    "נוכחות אהרונוביץ":  _sheet_url("1MAN8_OnQRBeiznYMvGa57GHU-xz-MErgFkkNOV_Ms8E"),
    "נוכחות פונקציונלי": _sheet_url("1LYqia2ESkLY0HD8QA0vkg1xxqLI5qx0nY9CVVj5MGGY"),
    "תוכניות אימון":     _sheet_url("1hi073ueyzdzEjzhP6a3ZgTPpeZDNzH2g2rKPj-L8a6I"),
    "מחנה קיץ":          _sheet_url("1hC9CZbXaFCUGvNHE96YVjv0HbEf4C5S_D4JOJFe1B4c"),
    "לילה יפני":         _sheet_url("1UMGrSnPcWp9lHX6DaaSt07ICNDUGEhk4O5v7L0hEsas"),
}


def sheets_links_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 נוכחות סירקין",    url=SHEET_LINKS["נוכחות סירקין"]),
            InlineKeyboardButton("📋 נוכחות חגור",      url=SHEET_LINKS["נוכחות חגור"]),
        ],
        [
            InlineKeyboardButton("📋 נווה ירק",         url=SHEET_LINKS["נוכחות נווה ירק"]),
            InlineKeyboardButton("📋 אהרונוביץ",        url=SHEET_LINKS["נוכחות אהרונוביץ"]),
        ],
        [
            InlineKeyboardButton("📋 פונקציונלי",       url=SHEET_LINKS["נוכחות פונקציונלי"]),
        ],
        [
            InlineKeyboardButton("🗓 תוכניות אימון",    url=SHEET_LINKS["תוכניות אימון"]),
        ],
        [
            InlineKeyboardButton("🏕 מחנה קיץ",         url=SHEET_LINKS["מחנה קיץ"]),
            InlineKeyboardButton("🌸 לילה יפני",        url=SHEET_LINKS["לילה יפני"]),
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

    # Training plan detection — user sends plan directly
    DIRECT_PLAN_KW = ("E2MOM", "E1MOM", "EMOM", "Bench Press", "Pull-Ups", "Box Jumps",
                      "Rope Climb", "DB Lunge", "Deadlift", "Squat", "Clean")
    if any(k.lower() in user_text.lower() for k in DIRECT_PLAN_KW) and not sheets_sessions.get(user_id):
        pending_plans[user_id] = {"reply": user_text, "original": user_text}
        save_json(PENDING_FILE, pending_plans)
        await update.message.reply_text(
            "💪 זיהיתי תוכנית אימון!\n\n*לשמור בגיליון?*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💾 כן, שמור בגיליון", callback_data="menu_plan_save")],
                [InlineKeyboardButton("לא תודה", callback_data="cancel_flow")],
            ])
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

    extra_context = ""
    for branch in ["חגור", "סירקין", "נווה ירק", "אהרונוביץ", "איפון פייט", "פונקציונלי", "נבחרת"]:
        if branch in user_text:
            for group in ["גנים", "א-ב", "א-ג", "ב-ד", "ג", "ג-ו", "ד-ו", "ד-ח", "ה-ז", "ז-ח", "ז-בוגרים", "ט-יב"]:
                if group in user_text:
                    extra_context = get_recent_trainings(branch, group)
                    break
            break

    # Inject live data when relevant
    data_context = _build_data_context(user_text)

    full_content = user_text
    if data_context:
        full_content = f"[נתונים]\n{data_context}\n\n{user_text}"
    elif extra_context:
        full_content = f"{user_text}\n\n[הקשר אוטומטי]\n{extra_context}"

    await update.message.chat.send_action("typing")

    try:
        reply = await call_claude(user_id, full_content)
    except Exception as e:
        log.error("Claude API error: %s", e)
        await update.message.reply_text("❌ שגיאה בתקשורת עם Claude. נסה שוב.")
        return

    # if Claude already returned a CSV (skipped the proposal step)
    if "```csv" in reply:
        csv_start = reply.index("```csv") + 6
        csv_end = reply.index("```", csv_start)
        csv_content = reply[csv_start:csv_end].strip()
        await deliver_csv(context, update.effective_chat.id, reply, csv_content)
    else:
        PLAN_KEYWORDS = ("חימום", "תרגול", "קרבות", "רנדורי", "כוח", "סיום", "EMOM", "E2MOM", "E1MOM")
        is_training_plan = sum(1 for k in PLAN_KEYWORDS if k in reply) >= 2

        if is_training_plan:
            pending_plans[user_id] = {"reply": reply, "original": user_text}
            save_json(PENDING_FILE, pending_plans)

        cal_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 הוסף ליומן", callback_data="quick_cal")]
        ])

        save_plan_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("💾 שמור בגיליון", callback_data="menu_plan_save"),
             InlineKeyboardButton("📅 הוסף ליומן", callback_data="quick_cal")],
        ])

        chunks = [reply[i:i+4096] for i in range(0, len(reply), 4096)]
        for i, chunk in enumerate(chunks):
            if i == len(chunks) - 1:
                if is_training_plan:
                    markup = save_plan_markup
                else:
                    markup = cal_markup
            else:
                markup = None
            await update.message.reply_text(chunk, reply_markup=markup)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    action = query.data

    # ─── Main menu callbacks ───
    if action == "noop":
        await query.answer()
        return

    if action == "menu_back":
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
        sheets_sessions[user_id] = {"step": "pw_branch"}
        await query.message.reply_text(
            "💾 *שמור תוכנית אימון בגיליון*\n\nאיזה סניף?",
            parse_mode="Markdown",
            reply_markup=_plan_branch_markup()
        )
        return

    # ── Plan wizard — branch ──
    if action.startswith("pw_branch|"):
        await query.answer()
        branch = action.split("|", 1)[1]
        ss = sheets_sessions.get(user_id, {})
        ss["branch"] = branch
        ss["step"] = "pw_group"
        sheets_sessions[user_id] = ss
        await query.message.reply_text(
            f"✅ {branch}\n\n👥 איזו קבוצה?",
            reply_markup=_plan_group_markup(branch)
        )
        return

    # ── Plan wizard — group ──
    if action.startswith("pw_group|"):
        await query.answer()
        group = action.split("|", 1)[1]
        ss = sheets_sessions.get(user_id, {})
        ss["group"] = group
        ss["step"] = "pw_date"
        sheets_sessions[user_id] = ss
        await query.message.reply_text(
            f"✅ {group}\n\n📅 תאריך האימון? (לדוגמה: `26/6` או `היום`)",
            parse_mode="Markdown",
            reply_markup=cancel_button()
        )
        return

    # ── Plan wizard — confirm save ──
    if action == "pw_confirm":
        await query.answer()
        ss = sheets_sessions.pop(user_id, {})
        await query.message.reply_text("⏳ שומר בגיליון...")
        await _plan_wizard_save(query.message, user_id, ss)
        return

    if action == "pw_reedit":
        await query.answer()
        ss = sheets_sessions.get(user_id, {})
        ss["step"] = "pw_edit"
        sheets_sessions[user_id] = ss
        await query.message.reply_text(
            "✏️ שלח שוב את התוכנית בניסוח אחר:",
            reply_markup=cancel_button()
        )
        return

    if action == "pw_cancel_edit":
        await query.answer()
        sheets_sessions.pop(user_id, None)
        await query.message.reply_text("בוטל.", reply_markup=cancel_button())
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
            "נווה ירק":  [("גנים","bw_group|גנים"), ("ג-ז","bw_group|ג-ז"), ("א-ב","bw_group|א-ב")],
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
            ("נווה ירק","ג-ז"):       [("שלישי", "17:45")],
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

    if action == "menu_help":
        await query.answer()
        help_text = (
            "💡 *מה אני יכול לעשות?*\n\n"
            "📅 *יומן* — מה יש לי היום/השבוע/החודש\n"
            "➕ *הוסף ליומן* — מחר ב-10:00 פגישה עם X\n"
            "🥋 *תוכנית אימון* — סירקין יום ב׳, ד-ו\n"
            "✅ *נוכחות* — נוכחות סירקין\n"
            "🏕️ *מחנה קיץ* — רשימת ילדים, עדכונים\n"
            "🌙 *לילה יפני* — רשימת משתתפים\n"
            "💬 *כל שאלה חופשית* — שאל אותי כל מה שתרצה\n\n"
            "או פשוט כתוב לי מה שאתה צריך 😊"
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
        day = day_names[__import__("datetime").datetime.now().weekday()]
        await update.message.reply_text(
            f"אין אימונים מתוכננים היום ({day}).\n"
            "ניתן לציין ידנית: *נוכחות סירקין ד-ו*",
            parse_mode="Markdown",
        )
        return True

    day_names = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
    day = day_names[__import__("datetime").datetime.now().weekday()]
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
            today = session.get("date", datetime.now().strftime("%d/%m/%Y"))
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
    await update.message.reply_text(reply)


async def _plan_wizard_extract(branch: str, group: str, plan_text: str) -> list[str]:
    """Use Claude to extract plan items in the correct format for the sheet."""
    import anthropic as _anthropic
    _client = _anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Determine row labels by branch/group
    if branch == "פונקציונלי":
        row_labels = ["חימום", "תרגול א", "תרגול ב", "תרגול ג", "תרגול ד", "כוח", "הערות", "סיום"]
    elif group in ("נבחרת", "ז-בוגרים", "ז-ח", "ט-יב"):
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
            plans_result = tp.design_all_tabs(delete_empty=True)
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

    if any(k in t for k in absence_keywords):
        try:
            log = load_json(Path("absence_log.json"), {})
            streaks = []
            for name, records in log.items():
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
        system = (
            "אתה עוזר אישי של טופז זבארי, מאמן ג'ודו. "
            "קיבלת נתונים מ-Google Calendar שלו. "
            "סכם את האירועים בצורה מסודרת ושימושית. "
            "הדגש אימוני ג'ודו, משימות דחופות ואירועים חשובים. "
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
        log = load_json(Path("absence_log.json"), {})
        alerts = []
        for name, records in log.items():
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
        day = day_names[__import__("datetime").datetime.now().weekday()]
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

    # ── Belt ceremony message ────────────────────────────────────────────────────
    # ── Plan wizard — date input ──────────────────────────────────────────────────
    if step == 'pw_date':
        from datetime import date as date_cls
        import re as _re
        if "היום" in text:
            plan_date = date_cls.today()
        elif "מחר" in text:
            from datetime import timedelta
            plan_date = date_cls.today() + timedelta(days=1)
        else:
            dm = _re.search(r'(\d{1,2})[/.](\d{1,2})', text)
            if not dm:
                await update.message.reply_text("❌ לא הבנתי תאריך. נסה: `26/6` או `היום`", parse_mode="Markdown")
                return True
            plan_date = date_cls(date_cls.today().year, int(dm.group(2)), int(dm.group(1)))
        ss["plan_date"] = plan_date.isoformat()
        ss["step"] = "pw_text"
        sheets_sessions[user_id] = ss
        await update.message.reply_text(
            f"✅ {plan_date.strftime('%d/%m/%Y')}\n\n"
            "📋 *עכשיו שלח את תוכנית האימון:*\n"
            "כתוב חופשי — הבוט יפרק אותה לפורמט הנכון",
            parse_mode="Markdown",
            reply_markup=cancel_button()
        )
        return True

    # ── Plan wizard — plan text input ─────────────────────────────────────────────
    if step == 'pw_text':
        ss["plan_text"] = text
        ss["step"] = "pw_preview"
        sheets_sessions[user_id] = ss
        branch = ss.get("branch", "")
        group  = ss.get("group", "")
        await update.message.reply_text("⏳ מפרק את התוכנית...")
        await _plan_wizard_preview(update.message, user_id, ss)
        return True

    # ── Plan wizard — edit after preview ─────────────────────────────────────────
    if step == 'pw_edit':
        ss["plan_text"] = text
        ss["step"] = "pw_preview"
        sheets_sessions[user_id] = ss
        await update.message.reply_text("⏳ מעדכן...")
        await _plan_wizard_preview(update.message, user_id, ss)
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
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    log.info("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
