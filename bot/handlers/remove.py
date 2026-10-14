from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler

from calendar_kb import build_calendar_keyboard, parse_calendar_callback
from db.repository import delete_diary_entry, ensure_user, get_entries_between, period_bounds
from handlers.diary import diary_command

CHOOSING_DAY = 0
BROWSING_CALENDAR = 1
CHOOSING_ENTRY = 2

CAL_PREFIX = "rmcal"


async def remove_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ensure_user(update.effective_user.id, update.effective_chat.id)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 Oggi", callback_data="rm:today"),
            InlineKeyboardButton("🗓 Altro giorno", callback_data="rm:other"),
        ]
    ])
    await update.message.reply_text("Da quale giorno vuoi togliere una voce?", reply_markup=keyboard)
    return CHOOSING_DAY


async def show_today_entries(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await _list_entries(query, update.effective_user.id, date.today())
    return CHOOSING_ENTRY


async def show_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    today = date.today()
    await query.edit_message_text(
        "Scegli il giorno:",
        reply_markup=build_calendar_keyboard(today.year, today.month, CAL_PREFIX),
    )
    return BROWSING_CALENDAR


async def handle_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    action, value = parse_calendar_callback(query.data, CAL_PREFIX)

    if action == "ignore":
        return BROWSING_CALENDAR

    if action == "nav":
        year, month = value
        await query.edit_message_reply_markup(
            reply_markup=build_calendar_keyboard(year, month, CAL_PREFIX)
        )
        return BROWSING_CALENDAR

    await _list_entries(query, update.effective_user.id, value)
    return CHOOSING_ENTRY


async def _list_entries(query, user_id: int, target_date: date) -> None:
    start, end = period_bounds("day", target_date)
    entries = get_entries_between(user_id, start, end)
    label = "oggi" if target_date == date.today() else f"il {target_date.strftime('%d/%m/%Y')}"

    if not entries:
        await query.edit_message_text(f"Non ci sono voci da togliere per {label}.")
        return

    buttons = []
    for entry in entries:
        ora = entry["logged_at"].astimezone().strftime("%H:%M")
        text = f"[{ora}] {entry['description']} ({entry['kcal']:.0f} kcal)"
        if len(text) > 60:
            text = text[:57] + "..."
        buttons.append([InlineKeyboardButton(text, callback_data=f"rment:{entry['id']}")])

    await query.edit_message_text(
        f"Cosa vuoi togliere di {label}?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def remove_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    entry_id = int(query.data.split(":")[1])

    deleted = delete_diary_entry(update.effective_user.id, entry_id)
    if deleted is None:
        await query.edit_message_text("Questa voce non c'e' piu' (forse gia' rimossa).")
    else:
        await query.edit_message_text(
            f"🗑️ Rimosso: {deleted['description']} ({deleted['kcal']:.0f} kcal)"
        )
    return ConversationHandler.END


async def fallback_to_diary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await diary_command(update, context)
    return ConversationHandler.END


remove_conversation = ConversationHandler(
    entry_points=[CommandHandler("remove", remove_start)],
    states={
        CHOOSING_DAY: [
            CallbackQueryHandler(show_today_entries, pattern="^rm:today$"),
            CallbackQueryHandler(show_calendar, pattern="^rm:other$"),
        ],
        BROWSING_CALENDAR: [
            CallbackQueryHandler(handle_calendar, pattern=f"^{CAL_PREFIX}:"),
        ],
        CHOOSING_ENTRY: [
            CallbackQueryHandler(remove_entry, pattern=r"^rment:\d+$"),
        ],
    },
    fallbacks=[CommandHandler("diary", fallback_to_diary)],
    allow_reentry=True,
)
