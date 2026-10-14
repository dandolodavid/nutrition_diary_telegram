from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from db.repository import ensure_user, get_foods
from food_cache import fold, has_quantity
from handlers.diary import diary_command, handle_message

SEARCHING = 0

# Telegram accetta messaggi fino a 4096 caratteri: si resta un po' sotto.
MAX_MESSAGE_CHARS = 3800
SEARCH_TIMEOUT_SECONDS = 120

SEARCH_PROMPT = (
    "🔎 Vuoi cercare qualcosa? Scrivimi una parola e filtro l'elenco "
    "(oppure manda /diary per tornare al diario)."
)


def _format_food(food: dict) -> str:
    return (
        f"• {food['name']} — {food['kcal_per_g'] * 100:.0f} kcal | "
        f"P {food['protein_per_g'] * 100:.1f} · C {food['carbs_per_g'] * 100:.1f} · "
        f"G {food['fat_per_g'] * 100:.1f}"
    )


def _chunks(header: str, lines: list[str]) -> list[str]:
    messages: list[str] = []
    current = header
    for line in lines:
        if len(current) + len(line) + 1 > MAX_MESSAGE_CHARS:
            messages.append(current)
            current = ""
        current += ("\n" if current else "") + line
    messages.append(current)
    return messages


async def _send_foods(update: Update, foods: list[dict], header: str) -> None:
    for message in _chunks(header, [_format_food(f) for f in foods]):
        await update.message.reply_text(message)


def filter_foods(foods: list[dict], term: str) -> list[dict]:
    words = fold(term).split()
    return [f for f in foods if all(w in fold(f["name"]) for w in words)]


async def _search_and_reply(update: Update, user_id: int, term: str) -> None:
    results = filter_foods(get_foods(user_id), term)
    if not results:
        await update.message.reply_text(f"Nessun alimento contiene «{term}».")
        return
    await _send_foods(
        update, results, f"🔎 {len(results)} risultati per «{term}» (valori per 100g):"
    )


async def db_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    ensure_user(user_id, update.effective_chat.id)

    if context.args:  # /db yogurt: ricerca diretta
        await _search_and_reply(update, user_id, " ".join(context.args))
        return ConversationHandler.END

    foods = get_foods(user_id)
    if not foods:
        await update.message.reply_text(
            "Il database alimenti e' ancora vuoto: si riempie da solo man mano che registri "
            "pasti con i grammi (es. «250 g yogurt greco»)."
        )
        return ConversationHandler.END

    await _send_foods(
        update, foods, f"📚 Il tuo database: {len(foods)} alimenti (valori per 100g)"
    )
    await update.message.reply_text(SEARCH_PROMPT)
    return SEARCHING


async def db_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    if has_quantity(text):
        # Un nome di alimento non contiene "250 g": e' un pasto, non una ricerca.
        await handle_message(update, context)
        return ConversationHandler.END
    await _search_and_reply(update, update.effective_user.id, text)
    return ConversationHandler.END


async def fallback_to_diary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await diary_command(update, context)
    return ConversationHandler.END


db_conversation = ConversationHandler(
    entry_points=[CommandHandler("db", db_start)],
    states={
        SEARCHING: [MessageHandler(filters.TEXT & ~filters.COMMAND, db_search)],
        ConversationHandler.TIMEOUT: [],
    },
    fallbacks=[CommandHandler("diary", fallback_to_diary)],
    conversation_timeout=SEARCH_TIMEOUT_SECONDS,
    allow_reentry=True,
)
