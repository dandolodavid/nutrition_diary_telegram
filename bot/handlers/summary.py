from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler

from calendar_kb import build_calendar_keyboard, parse_calendar_callback
from db.repository import ensure_user, get_items_between, period_bounds
from handlers.diary import diary_command

CHOOSING_DAY = 0
BROWSING_CALENDAR = 1

CAL_PREFIX = "sumcal"


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ensure_user(update.effective_user.id, update.effective_chat.id)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 Oggi", callback_data="sum:today"),
            InlineKeyboardButton("🗓 Altro giorno", callback_data="sum:other"),
        ]
    ])
    await update.message.reply_text("Il riepilogo di oggi o di un altro giorno?", reply_markup=keyboard)
    return CHOOSING_DAY


async def show_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await _send_summary(query, update.effective_user.id, date.today())
    return ConversationHandler.END


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

    await _send_summary(query, update.effective_user.id, value)
    return ConversationHandler.END


async def _send_summary(query, user_id: int, target_date: date) -> None:
    start, end = period_bounds("day", target_date)
    items = get_items_between(user_id, start, end)
    label = "oggi" if target_date == date.today() else f"il {target_date.strftime('%d/%m/%Y')}"

    if not items:
        await query.edit_message_text(f"Non hai registrato nulla per {label}.")
        return

    lines = [f"📋 Cosa hai mangiato {label}:\n"]
    total_kcal = total_protein = total_carbs = total_fat = 0.0
    for item in items:
        ora = item["logged_at"].astimezone().strftime("%H:%M")
        lines.append(
            f"- [{ora}] {item['name']}: {item['kcal']:.0f} kcal, {item['protein_g']:.0f}g "
            f"proteine, {item['carbs_g']:.0f}g carboidrati, {item['fat_g']:.0f}g grassi"
        )
        total_kcal += item["kcal"]
        total_protein += item["protein_g"]
        total_carbs += item["carbs_g"]
        total_fat += item["fat_g"]

    lines.append("")
    lines.append(
        f"Totale giornata: {total_kcal:.0f} kcal | {total_protein:.0f}g proteine | "
        f"{total_carbs:.0f}g carboidrati | {total_fat:.0f}g grassi"
    )

    await query.edit_message_text("\n".join(lines))


async def fallback_to_diary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await diary_command(update, context)
    return ConversationHandler.END


summary_conversation = ConversationHandler(
    entry_points=[CommandHandler("summary", summary_command)],
    states={
        CHOOSING_DAY: [
            CallbackQueryHandler(show_today, pattern="^sum:today$"),
            CallbackQueryHandler(show_calendar, pattern="^sum:other$"),
        ],
        BROWSING_CALENDAR: [
            CallbackQueryHandler(handle_calendar, pattern=f"^{CAL_PREFIX}:"),
        ],
    },
    fallbacks=[CommandHandler("diary", fallback_to_diary)],
    allow_reentry=True,
)
