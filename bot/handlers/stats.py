from collections import defaultdict
from datetime import date, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler

from calendar_kb import MONTHS_IT, build_calendar_keyboard, parse_calendar_callback
from charts import build_comparison_chart
from db.repository import ensure_user, get_entries_between, get_targets, period_bounds
from handlers.diary import diary_command

CHOOSING_PERIOD = 0
BROWSING_CALENDAR = 1
CHOOSING_CUSTOM_PERIOD = 2

CAL_PREFIX = "statcal"

PERIOD_LABELS = {"day": "oggi", "week": "questa settimana", "month": "questo mese"}
CUSTOM_PERIOD_LABELS = {"day": "Giorno", "week": "Settimana", "month": "Mese"}


async def stats_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ensure_user(update.effective_user.id, update.effective_chat.id)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Giorno", callback_data="day"),
                InlineKeyboardButton("Settimana", callback_data="week"),
                InlineKeyboardButton("Mese", callback_data="month"),
            ],
            [InlineKeyboardButton("📅 Data specifica", callback_data="custom")],
        ]
    )
    await update.message.reply_text("Per quale periodo vuoi le statistiche?", reply_markup=keyboard)
    return CHOOSING_PERIOD


def _totals(entries: list[dict]) -> dict:
    totals = defaultdict(float)
    for entry in entries:
        totals["kcal"] += entry["kcal"]
        totals["protein_g"] += entry["protein_g"]
        totals["carbs_g"] += entry["carbs_g"]
        totals["fat_g"] += entry["fat_g"]
    return dict(totals)


def _days_with_entries(entries: list[dict]) -> int:
    """Numero di giorni di calendario distinti in cui c'e' almeno un pasto registrato
    (non i giorni trascorsi nel periodo: se salti dei giorni non devono abbassare la media)."""
    dates = {entry["logged_at"].astimezone().date() for entry in entries}
    return max(1, len(dates))


def _period_label(period: str, ref_date: date) -> str:
    if ref_date == date.today():
        return PERIOD_LABELS[period]
    if period == "day":
        return f"il {ref_date.strftime('%d/%m/%Y')}"
    if period == "week":
        start, _ = period_bounds("week", ref_date)
        end_incl = start.date() + timedelta(days=6)
        return f"la settimana dal {start.date().strftime('%d/%m')} al {end_incl.strftime('%d/%m/%Y')}"
    if period == "month":
        return f"{MONTHS_IT[ref_date.month - 1]} {ref_date.year}"
    raise ValueError(f"periodo sconosciuto: {period}")


async def _send_stats(query, user_id: int, period: str, ref_date: date) -> None:
    start, end = period_bounds(period, ref_date)
    entries = get_entries_between(user_id, start, end)
    target = get_targets(user_id)
    totals = _totals(entries)

    if period == "day":
        actual = totals
        note = ""
    else:
        days = _days_with_entries(entries)
        actual = {k: v / days for k, v in totals.items()} if totals else {}
        note = f" (media giornaliera su {days} giorni con inserimenti)"

    label = _period_label(period, ref_date)

    if not entries:
        await query.edit_message_text(f"Nessun pasto registrato per {label}.")
        return

    lines = [f"📊 Andamento {label}{note}:\n"]
    for key, human in [
        ("kcal", "Kcal"),
        ("protein_g", "Proteine (g)"),
        ("carbs_g", "Carboidrati (g)"),
        ("fat_g", "Grassi (g)"),
    ]:
        value = actual.get(key, 0)
        if target:
            tgt = target[key]
            pct = (value / tgt * 100) if tgt else 0
            lines.append(f"{human}: {value:.0f} / {tgt:.0f} ({pct:.0f}%)")
        else:
            lines.append(f"{human}: {value:.0f}")

    if not target:
        lines.append("\n⚠️ Nessun target impostato — usa /set per confrontarlo.")

    await query.edit_message_text("\n".join(lines))

    chart_buf = build_comparison_chart(actual, target, f"Andamento {label}")
    await query.get_bot().send_photo(chat_id=query.message.chat_id, photo=chart_buf)


async def show_period(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await _send_stats(query, update.effective_user.id, query.data, date.today())
    return ConversationHandler.END


async def show_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    today = date.today()
    await query.edit_message_text(
        "Scegli una data di riferimento:",
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

    context.user_data["stats_ref_date"] = value
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(CUSTOM_PERIOD_LABELS["day"], callback_data="pday"),
                InlineKeyboardButton(CUSTOM_PERIOD_LABELS["week"], callback_data="pweek"),
                InlineKeyboardButton(CUSTOM_PERIOD_LABELS["month"], callback_data="pmonth"),
            ]
        ]
    )
    await query.edit_message_text(
        f"Per il {value.strftime('%d/%m/%Y')}: giorno singolo, la settimana o il mese che lo contiene?",
        reply_markup=keyboard,
    )
    return CHOOSING_CUSTOM_PERIOD


async def show_custom_period(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    period = query.data[1:]  # "pday" -> "day", "pweek" -> "week", "pmonth" -> "month"
    ref_date = context.user_data.pop("stats_ref_date")
    await _send_stats(query, update.effective_user.id, period, ref_date)
    return ConversationHandler.END


async def fallback_to_diary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await diary_command(update, context)
    return ConversationHandler.END


stats_conversation = ConversationHandler(
    entry_points=[CommandHandler("stats", stats_start)],
    states={
        CHOOSING_PERIOD: [
            CallbackQueryHandler(show_period, pattern="^(day|week|month)$"),
            CallbackQueryHandler(show_calendar, pattern="^custom$"),
        ],
        BROWSING_CALENDAR: [
            CallbackQueryHandler(handle_calendar, pattern=f"^{CAL_PREFIX}:"),
        ],
        CHOOSING_CUSTOM_PERIOD: [
            CallbackQueryHandler(show_custom_period, pattern="^p(day|week|month)$"),
        ],
    },
    fallbacks=[CommandHandler("diary", fallback_to_diary)],
    allow_reentry=True,
)
