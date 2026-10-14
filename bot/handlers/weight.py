from collections import defaultdict
from datetime import date, datetime, timedelta

from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from charts import build_weight_trend_chart
from db.repository import ensure_user, get_weight_entries_since, upsert_weight_entry
from handlers.diary import diary_command

WAITING_WEIGHT = 0

WEEKS_HISTORY = 8


def _parse_weight(text: str) -> float | None:
    try:
        value = float(text.strip().replace(",", "."))
    except ValueError:
        return None
    if value <= 0 or value > 500:
        return None
    return value


async def _save_and_confirm(update: Update, telegram_user_id: int, weight_kg: float) -> None:
    upsert_weight_entry(telegram_user_id, weight_kg)
    await update.message.reply_text(f"Peso salvato ✅ {weight_kg:.1f} kg")


async def weight_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ensure_user(update.effective_user.id, update.effective_chat.id)

    if context.args:
        weight = _parse_weight(" ".join(context.args))
        if weight is None:
            await update.message.reply_text("Peso non valido. Es: /weight 78.5")
            return ConversationHandler.END
        await _save_and_confirm(update, update.effective_user.id, weight)
        return ConversationHandler.END

    await update.message.reply_text("Inviami il tuo peso di oggi in kg (es. 78.5).")
    return WAITING_WEIGHT


async def receive_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    weight = _parse_weight(update.message.text)
    if weight is None:
        await update.message.reply_text("Non ho capito il peso. Inviami un numero, es. 78.5.")
        return WAITING_WEIGHT

    await _save_and_confirm(update, update.effective_user.id, weight)
    return ConversationHandler.END


async def fallback_to_diary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await diary_command(update, context)
    return ConversationHandler.END


weight_conversation = ConversationHandler(
    entry_points=[CommandHandler("weight", weight_command)],
    states={
        WAITING_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_weight)],
    },
    fallbacks=[CommandHandler("diary", fallback_to_diary)],
    allow_reentry=True,
)


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


async def check_weight_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    ensure_user(user_id, update.effective_chat.id)

    today = datetime.now().astimezone().date()
    since_date = _week_start(today) - timedelta(weeks=WEEKS_HISTORY - 1)

    entries = get_weight_entries_since(user_id, since_date)

    if not entries:
        await update.message.reply_text(
            f"Nessun peso registrato nelle ultime {WEEKS_HISTORY} settimane. Usa /weight per iniziare."
        )
        return

    weekly_values: dict[date, list[float]] = defaultdict(list)
    for entry in entries:
        weekly_values[_week_start(entry["log_date"])].append(entry["weight_kg"])

    # Media solo sui giorni in cui il peso e' stato inserito: le settimane senza dati non
    # abbassano/alzano la media appiattendo il trend con giorni fittizi.
    weekly_points = [
        (week_start, sum(values) / len(values))
        for week_start, values in sorted(weekly_values.items())
    ]
    daily_points = [(e["log_date"], e["weight_kg"]) for e in entries]

    lines = [f"⚖️ Andamento peso (media settimanale, ultime {WEEKS_HISTORY} settimane):\n"]
    for week_start, avg in weekly_points:
        week_end = week_start + timedelta(days=6)
        n_days = len(weekly_values[week_start])
        lines.append(
            f"- {week_start.strftime('%d/%m')} - {week_end.strftime('%d/%m')}: "
            f"{avg:.1f} kg ({n_days} gg registrati)"
        )

    if len(weekly_points) >= 2:
        delta = weekly_points[-1][1] - weekly_points[0][1]
        lines.append(f"\nVariazione nel periodo: {delta:+.1f} kg")

    await update.message.reply_text("\n".join(lines))

    chart_buf = build_weight_trend_chart(
        daily_points, weekly_points, f"Andamento peso - ultime {WEEKS_HISTORY} settimane"
    )
    await context.bot.send_photo(chat_id=update.effective_chat.id, photo=chart_buf)
