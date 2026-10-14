import asyncio
import logging

from telegram import BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from config import TELEGRAM_BOT_TOKEN
from food_cache import bootstrap
from handlers.diary import diary_command, handle_message
from handlers.edit_foods import edit_foods_conversation
from handlers.foods import db_conversation
from handlers.remove import remove_conversation
from handlers.set_targets import set_targets_conversation
from handlers.start import start
from handlers.stats import stats_conversation
from handlers.summary import summary_conversation
from handlers.weight import check_weight_command, weight_conversation

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

COMMANDS = [
    BotCommand("start", "Recap: cosa fa questo bot"),
    BotCommand("diary", "Torna alla modalita diario (foto/testo pasto)"),
    BotCommand("set", "Imposta i target giornalieri (manuale o da PDF dieta)"),
    BotCommand("stats", "Statistiche giorno/settimana/mese vs target"),
    BotCommand("summary", "Cosa hai mangiato: oggi o un altro giorno, alimento per alimento"),
    BotCommand("remove", "Togli una voce registrata (oggi o un altro giorno)"),
    BotCommand("db", "Il tuo database alimenti (valori al grammo) con ricerca"),
    BotCommand("edit_db", "Modifica nome o valori di un alimento del database (o eliminalo)"),
    BotCommand("weight", "Registra il peso di oggi"),
    BotCommand("check_weight", "Trend del peso (media settimanale, ultime 8 settimane)"),
]


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands(COMMANDS)
    # Tabelle nuove su DB esistente + database alimenti iniziale dallo storico.
    await asyncio.to_thread(bootstrap)


def main() -> None:
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(set_targets_conversation)
    application.add_handler(stats_conversation)
    application.add_handler(CommandHandler("diary", diary_command))
    application.add_handler(summary_conversation)
    application.add_handler(remove_conversation)
    application.add_handler(db_conversation)
    application.add_handler(edit_foods_conversation)
    application.add_handler(weight_conversation)
    application.add_handler(CommandHandler("check_weight", check_weight_command))
    application.add_handler(
        MessageHandler(
            filters.PHOTO | filters.VOICE | filters.AUDIO | (filters.TEXT & ~filters.COMMAND),
            handle_message,
        )
    )

    application.run_polling()


if __name__ == "__main__":
    main()
