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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = open("system_prompt.txt", encoding="utf-8").read()

HISTORY_FILE = Path("conversation_history.json")
LOG_FILE = Path("training_log.json")
PENDING_FILE = Path("pending_plans.json")


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


def plan_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
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
        [InlineKeyboardButton("📋 תוכנית חדשה", callback_data="new_plan")],
    ])


async def send_long(update: Update, text: str, reply_markup=None):
    chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
    for i, chunk in enumerate(chunks):
        markup = reply_markup if i == len(chunks) - 1 else None
        await update.message.reply_text(chunk, reply_markup=markup)


async def call_claude(user_id: str, user_content: str) -> str:
    append_history(user_id, "user", user_content)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=get_history(user_id),
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 שלום! אני סוכן תוכניות האימון של מועדון וולבס.\n\n"
        "שלח לי בקשה כמו:\n"
        "  • *חגור יום א׳, גנים + א-ג*\n"
        "  • *סירקין יום ב׳, ד-ו, יש תחרות בעוד שבועיים*\n\n"
        "אציע תוכנית — תאשר בכפתור או תבקש שינויים.",
        parse_mode="Markdown",
    )


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

    # Text triggers for camp/lyla menus
    user_text = update.message.text.strip()
    if any(t in user_text for t in ("מחנה קיץ", "מחנה", "camp")):
        await camp_command(update, context)
        return
    if any(t in user_text for t in ("לילה יפני", "לילה")):
        await lyla_command(update, context)
        return

    user_id = str(update.effective_user.id)
    user_text = update.message.text

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
        # store as pending and show approval buttons
        pending_plans[user_id] = reply
        save_json(PENDING_FILE, pending_plans)
        chunks = [reply[i:i+4096] for i in range(0, len(reply), 4096)]
        for i, chunk in enumerate(chunks):
            markup = plan_buttons() if i == len(chunks) - 1 else None
            await update.message.reply_text(chunk, reply_markup=markup)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    action = query.data

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
            cs["step"] = "wait_calendar"
            await update.message.reply_text(
                "📂 *באיזה יומן לשמור?*",
                parse_mode="Markdown",
                reply_markup=calendar_buttons(),
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
    await update.message.reply_text("✏️ *מה הכותרת של המשימה?*", parse_mode="Markdown")
    return True


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
        result = f"✅ עיצוב הוחל על {count} גיליונות"
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
    user_id = str(update.effective_user.id)
    await update.message.chat.send_action("typing")
    try:
        date_from, date_to = cal.parse_date_range_hebrew(query_text)
        events = cal.get_events_range(date_from, date_to)
        events_text = cal.format_events_for_claude(events, date_from, date_to)
        prompt = (
            f"[יומן Google — נתונים חיים]\n{events_text}\n\n"
            f"שאלה: {query_text}\n\n"
            "נתח את האירועים האלה והצג סיכום מסודר ושימושי. "
            "ציין אימונים, משימות ג'ודו, פגישות ואירועים אישיים. "
            "הדגש דברים דחופים. תן המלצות אם רלוונטי. ענה בעברית."
        )
        reply = await call_claude(user_id, prompt)
    except Exception as e:
        log.error("Calendar query error: %s", e)
        reply = f"❌ שגיאה בשליפת היומן: {e}"
    chunks = [reply[i:i+4096] for i in range(0, len(reply), 4096)]
    for chunk in chunks:
        await update.message.reply_text(chunk)


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
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
