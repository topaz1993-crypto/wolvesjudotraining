"""
Wolves Judo — Training Plan Agent (Telegram Bot)
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

    full_content = user_text
    if extra_context:
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
    await query.answer()
    user_id = str(query.from_user.id)
    action = query.data

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


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    history[user_id] = []
    pending_plans.pop(user_id, None)
    save_json(HISTORY_FILE, history)
    save_json(PENDING_FILE, pending_plans)
    await update.message.reply_text("🔄 שיחה אופסה.")


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
