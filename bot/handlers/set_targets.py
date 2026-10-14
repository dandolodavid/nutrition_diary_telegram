import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from agents.diet_agent import run_diet_agent
from db.repository import ensure_user, upsert_targets
from handlers.diary import diary_command
from pdf_ingest import extract_text

CHOOSING, MANUAL_INPUT, WAITING_PDF, PDF_CONFIRM = range(4)


async def set_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ensure_user(update.effective_user.id, update.effective_chat.id)
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ Imposta a mano", callback_data="manual")],
            [InlineKeyboardButton("📄 Carica PDF dieta", callback_data="pdf")],
        ]
    )
    await update.message.reply_text(
        "Come vuoi impostare i tuoi target giornalieri?", reply_markup=keyboard
    )
    return CHOOSING


async def choose_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Inviami kcal, proteine, carboidrati e grassi in un unico messaggio, in quest'ordine,\n"
        "es: `2200 150 220 70`",
        parse_mode="Markdown",
    )
    return MANUAL_INPUT


async def choose_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Inviami il PDF della tua dieta come documento.")
    return WAITING_PDF


async def receive_manual_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    parts = update.message.text.replace(",", ".").split()
    if len(parts) != 4:
        await update.message.reply_text(
            "Formato non valido. Inviami 4 numeri: kcal proteine carboidrati grassi."
        )
        return MANUAL_INPUT

    try:
        kcal, protein_g, carbs_g, fat_g = (float(p) for p in parts)
    except ValueError:
        await update.message.reply_text(
            "Non ho capito i numeri. Inviami 4 numeri: kcal proteine carboidrati grassi."
        )
        return MANUAL_INPUT

    upsert_targets(
        telegram_user_id=update.effective_user.id,
        kcal=kcal,
        protein_g=protein_g,
        carbs_g=carbs_g,
        fat_g=fat_g,
        source="manual",
    )
    await update.message.reply_text(
        f"Target salvati ✅\nKcal: {kcal:.0f} | Proteine: {protein_g:.0f} g | "
        f"Carboidrati: {carbs_g:.0f} g | Grassi: {fat_g:.0f} g"
    )
    return ConversationHandler.END


async def receive_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    document = update.message.document
    file = await context.bot.get_file(document.file_id)
    pdf_bytes = bytes(await file.download_as_bytearray())

    await update.message.reply_text("Leggo il PDF e stimo i target, un attimo...")
    diet_text = await asyncio.to_thread(extract_text, pdf_bytes)

    try:
        response = await asyncio.to_thread(
            run_diet_agent,
            diet_text,
            user_id=str(update.effective_user.id),
        )
    except Exception:
        await update.message.reply_text(
            "⚠️ Non sono riuscito a contattare l'assistente AI per leggere il PDF. Riprova tra "
            "qualche secondo con /set."
        )
        return ConversationHandler.END

    targets = response.content
    context.user_data["pending_diet_targets"] = targets

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Conferma", callback_data="confirm_pdf")],
            [InlineKeyboardButton("❌ Annulla", callback_data="cancel_pdf")],
        ]
    )
    await update.message.reply_text(
        f"Dal PDF ho stimato:\n\n"
        f"Kcal: {targets.daily_kcal:.0f}\n"
        f"Proteine: {targets.protein_g:.0f} g\n"
        f"Carboidrati: {targets.carbs_g:.0f} g\n"
        f"Grassi: {targets.fat_g:.0f} g\n\n"
        f"{targets.summary}\n\nConfermi questi target?",
        reply_markup=keyboard,
    )
    return PDF_CONFIRM


async def confirm_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    targets = context.user_data.pop("pending_diet_targets", None)
    if targets is None:
        await query.edit_message_text("Nessun target in sospeso, riprova con /set.")
        return ConversationHandler.END

    upsert_targets(
        telegram_user_id=update.effective_user.id,
        kcal=targets.daily_kcal,
        protein_g=targets.protein_g,
        carbs_g=targets.carbs_g,
        fat_g=targets.fat_g,
        source="diet_pdf",
    )
    await query.edit_message_text("Target dalla dieta salvati ✅")
    return ConversationHandler.END


async def cancel_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("pending_diet_targets", None)
    await query.edit_message_text("Ok, target non salvati. Rilancia /set quando vuoi.")
    return ConversationHandler.END


async def fallback_to_diary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await diary_command(update, context)
    return ConversationHandler.END


set_targets_conversation = ConversationHandler(
    entry_points=[CommandHandler("set", set_start)],
    states={
        CHOOSING: [
            CallbackQueryHandler(choose_manual, pattern="^manual$"),
            CallbackQueryHandler(choose_pdf, pattern="^pdf$"),
        ],
        MANUAL_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_manual_input)],
        WAITING_PDF: [MessageHandler(filters.Document.PDF, receive_pdf)],
        PDF_CONFIRM: [
            CallbackQueryHandler(confirm_pdf, pattern="^confirm_pdf$"),
            CallbackQueryHandler(cancel_pdf, pattern="^cancel_pdf$"),
        ],
    },
    fallbacks=[CommandHandler("diary", fallback_to_diary)],
    allow_reentry=True,
)
