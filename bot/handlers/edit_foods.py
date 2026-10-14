import re

import psycopg
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from db.repository import delete_food, ensure_user, get_food, get_foods, update_food
from food_cache import has_quantity, name_key, name_tokens
from handlers.diary import diary_command, handle_message
from handlers.foods import filter_foods

SEARCHING, MENU, ASK_NAME, ASK_VALUES, CONFIRM_DELETE = range(5)

FOOD_ID_KEY = "edit_food_id"
MAX_BUTTONS = 12
MAX_NAME_CHARS = 80
EDIT_TIMEOUT_SECONDS = 180

# Limiti fisici per 100g: oltre non sono valori nutrizionali ma errori di battitura.
MAX_KCAL_PER_100G = 900
MAX_MACRO_PER_100G = 100

SOURCE_LABELS = {"history": "storico", "llm": "stimato dall'AI", "manual": "modificato a mano"}

CANCEL_HINT = "(oppure manda /diary per annullare)"


def _per_100g(food: dict) -> tuple[float, float, float, float]:
    return (
        food["kcal_per_g"] * 100,
        food["protein_per_g"] * 100,
        food["carbs_per_g"] * 100,
        food["fat_per_g"] * 100,
    )


def _values_text(food: dict) -> str:
    kcal, protein, carbs, fat = _per_100g(food)
    return f"{kcal:.0f} kcal | P {protein:.1f} · C {carbs:.1f} · G {fat:.1f}"


def _detail(food: dict) -> str:
    lines = [f"📝 {food['name']}", f"Per 100g: {_values_text(food)}",
             f"Origine: {SOURCE_LABELS.get(food['source'], food['source'])}"]
    if food["aliases"]:
        lines.append("Riconosciuto anche come: " + ", ".join(food["aliases"]))
    lines.append("\nLe voci gia' registrate nel diario non cambiano.")
    return "\n".join(lines)


def _menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Nome", callback_data="edb:name"),
            InlineKeyboardButton("📊 Valori", callback_data="edb:vals"),
        ],
        [
            InlineKeyboardButton("🗑 Elimina", callback_data="edb:del"),
            InlineKeyboardButton("✖ Chiudi", callback_data="edb:close"),
        ],
    ])


def _parse_values(text: str) -> tuple[float, float, float, float] | str:
    """Quattro numeri per 100g (kcal, proteine, carboidrati, grassi) separati da spazio,
    ';' o '/'. Ritorna i valori oppure il messaggio d'errore da mostrare."""
    tokens = [t for t in re.split(r"[\s;/]+", text.strip()) if t]
    if len(tokens) != 4 or not all(re.fullmatch(r"\d+(?:[.,]\d+)?", t) for t in tokens):
        return ("Servono esattamente 4 numeri per 100g: kcal, proteine, carboidrati, grassi "
                "(es. «52 9 4 0»).")
    kcal, protein, carbs, fat = (float(t.replace(",", ".")) for t in tokens)
    if kcal > MAX_KCAL_PER_100G or max(protein, carbs, fat) > MAX_MACRO_PER_100G:
        return (f"Valori impossibili per 100g (max {MAX_KCAL_PER_100G} kcal e "
                f"{MAX_MACRO_PER_100G}g per ogni macro). Controlla e riprova.")
    return kcal, protein, carbs, fat


async def _results(message, user_id: int, term: str) -> int:
    results = filter_foods(get_foods(user_id), term)
    if not results:
        await message.reply_text(
            f"Nessun alimento contiene «{term}». Prova un'altra parola {CANCEL_HINT}."
        )
        return SEARCHING

    header = f"Quale alimento vuoi modificare? ({len(results)} risultati per «{term}»)"
    if len(results) > MAX_BUTTONS:
        header += f"\nNe mostro {MAX_BUTTONS}: scrivi un'altra parola per restringere."
    buttons = []
    for food in results[:MAX_BUTTONS]:
        text = f"{food['name']} — {food['kcal_per_g'] * 100:.0f} kcal"
        if len(text) > 60:
            text = text[:57] + "..."
        buttons.append([InlineKeyboardButton(text, callback_data=f"edb:pick:{food['id']}")])
    buttons.append([InlineKeyboardButton("✖ Annulla", callback_data="edb:close")])
    await message.reply_text(header, reply_markup=InlineKeyboardMarkup(buttons))
    return SEARCHING


async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    ensure_user(user_id, update.effective_chat.id)
    context.user_data.pop(FOOD_ID_KEY, None)

    if not get_foods(user_id):
        await update.message.reply_text(
            "Il database alimenti e' ancora vuoto: non c'e' niente da modificare."
        )
        return ConversationHandler.END
    if context.args:
        return await _results(update.message, user_id, " ".join(context.args))
    await update.message.reply_text(
        f"✏️ Che alimento vuoi modificare? Scrivimi una parola per cercarlo {CANCEL_HINT}."
    )
    return SEARCHING


async def _meal_instead(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Un testo con "250 g ..." e' un pasto, non un nome/valori: lo si registra e si esce."""
    await handle_message(update, context)
    return ConversationHandler.END


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if has_quantity(update.message.text):
        return await _meal_instead(update, context)
    return await _results(update.message, update.effective_user.id, update.message.text)


async def _current_food(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    food_id = context.user_data.get(FOOD_ID_KEY)
    return get_food(update.effective_user.id, food_id) if food_id else None


async def _gone(update: Update) -> int:
    text = "Questo alimento non c'e' piu' (forse gia' eliminato)."
    if update.callback_query:
        await update.callback_query.edit_message_text(text)
    else:
        await update.message.reply_text(text)
    return ConversationHandler.END


async def pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    food = get_food(update.effective_user.id, int(query.data.split(":")[2]))
    if food is None:
        return await _gone(update)
    context.user_data[FOOD_ID_KEY] = food["id"]
    await query.edit_message_text(_detail(food), reply_markup=_menu_markup())
    return MENU


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    food = await _current_food(update, context)
    if food is None:
        return await _gone(update)
    await query.edit_message_text(
        f"Nome attuale: {food['name']}\nScrivimi il nuovo nome {CANCEL_HINT}."
    )
    return ASK_NAME


async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if has_quantity(text):
        return await _meal_instead(update, context)
    food = await _current_food(update, context)
    if food is None:
        return await _gone(update)

    if not name_tokens(text) or len(text) > MAX_NAME_CHARS or any(c in text for c in "(),"):
        await update.message.reply_text(
            f"Nome non valido: serve almeno una parola, max {MAX_NAME_CHARS} caratteri, "
            f"senza virgole o parentesi. Riprova {CANCEL_HINT}."
        )
        return ASK_NAME

    key = name_key(text)
    clash = next((f for f in get_foods(update.effective_user.id)
                  if f["name_key"] == key and f["id"] != food["id"]), None)
    if clash is not None:
        await update.message.reply_text(
            f"Esiste gia' «{clash['name']}» che per me e' lo stesso nome. Scegline un altro, "
            f"oppure modifica o elimina quello {CANCEL_HINT}."
        )
        return ASK_NAME
    try:
        update_food(update.effective_user.id, food["id"], name=text, name_key=key)
    except psycopg.errors.UniqueViolation:
        await update.message.reply_text("Esiste gia' un alimento con questo nome. Riprova.")
        return ASK_NAME

    await update.message.reply_text(
        "✅ Nome aggiornato.\n\n" + _detail(await _current_food(update, context)),
        reply_markup=_menu_markup(),
    )
    return MENU


async def ask_values(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    food = await _current_food(update, context)
    if food is None:
        return await _gone(update)
    kcal, protein, carbs, fat = _per_100g(food)
    await query.edit_message_text(
        f"{food['name']}\nValori attuali per 100g: {kcal:.0f} {protein:.1f} {carbs:.1f} {fat:.1f}\n\n"
        "Mandami i nuovi valori per 100g, nell'ordine kcal, proteine, carboidrati, grassi "
        f"(es. «52 9 4 0») {CANCEL_HINT}."
    )
    return ASK_VALUES


async def receive_values(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    if has_quantity(text):
        return await _meal_instead(update, context)
    food = await _current_food(update, context)
    if food is None:
        return await _gone(update)

    parsed = _parse_values(text)
    if isinstance(parsed, str):
        await update.message.reply_text(f"{parsed} {CANCEL_HINT}")
        return ASK_VALUES

    update_food(update.effective_user.id, food["id"], per_g=tuple(v / 100 for v in parsed))
    await update.message.reply_text(
        "✅ Valori aggiornati.\n\n" + _detail(await _current_food(update, context)),
        reply_markup=_menu_markup(),
    )
    return MENU


async def ask_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    food = await _current_food(update, context)
    if food is None:
        return await _gone(update)
    await query.edit_message_text(
        f"Eliminare «{food['name']}» dal database?\nLe voci gia' nel diario restano.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Si, elimina", callback_data="edb:delyes"),
            InlineKeyboardButton("↩️ Annulla", callback_data="edb:back"),
        ]]),
    )
    return CONFIRM_DELETE


async def do_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    food = await _current_food(update, context)
    if food is None:
        return await _gone(update)
    delete_food(update.effective_user.id, food["id"])
    context.user_data.pop(FOOD_ID_KEY, None)
    await query.edit_message_text(f"🗑️ Eliminato: {food['name']}")
    return ConversationHandler.END


async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    food = await _current_food(update, context)
    if food is None:
        return await _gone(update)
    await query.edit_message_text(_detail(food), reply_markup=_menu_markup())
    return MENU


async def close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.pop(FOOD_ID_KEY, None)
    await query.edit_message_text("Ok, chiuso.")
    return ConversationHandler.END


async def fallback_to_diary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(FOOD_ID_KEY, None)
    await diary_command(update, context)
    return ConversationHandler.END


_TEXT = filters.TEXT & ~filters.COMMAND
_CLOSE = CallbackQueryHandler(close, pattern=r"^edb:close$")

edit_foods_conversation = ConversationHandler(
    entry_points=[CommandHandler("edit_db", edit_start)],
    states={
        SEARCHING: [
            MessageHandler(_TEXT, search),
            CallbackQueryHandler(pick, pattern=r"^edb:pick:\d+$"),
            _CLOSE,
        ],
        MENU: [
            CallbackQueryHandler(ask_name, pattern=r"^edb:name$"),
            CallbackQueryHandler(ask_values, pattern=r"^edb:vals$"),
            CallbackQueryHandler(ask_delete, pattern=r"^edb:del$"),
            _CLOSE,
        ],
        ASK_NAME: [MessageHandler(_TEXT, receive_name)],
        ASK_VALUES: [MessageHandler(_TEXT, receive_values)],
        CONFIRM_DELETE: [
            CallbackQueryHandler(do_delete, pattern=r"^edb:delyes$"),
            CallbackQueryHandler(back_to_menu, pattern=r"^edb:back$"),
        ],
        ConversationHandler.TIMEOUT: [],
    },
    fallbacks=[CommandHandler("diary", fallback_to_diary)],
    conversation_timeout=EDIT_TIMEOUT_SECONDS,
    allow_reentry=True,
)
