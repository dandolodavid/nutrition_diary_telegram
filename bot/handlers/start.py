from telegram import Update
from telegram.ext import ContextTypes

from db.repository import ensure_user

RECAP_MESSAGE = """Ciao! Sono il tuo diario alimentare 🍽️

- Mandami una *foto* del piatto, *scrivi* o *mandami un vocale* con cosa hai mangiato: \
stimo kcal, proteine, carboidrati e grassi. Se dalla foto ho dei dubbi te li chiedo prima \
di stimare.
- /set — imposta i tuoi target giornalieri (a mano, oppure caricando il PDF della tua dieta).
- /stats — vedi l'andamento di oggi, della settimana o del mese rispetto ai tuoi target.
- /summary — vedi cosa hai mangiato oggi, alimento per alimento.
- /db — il tuo database di alimenti al grammo (li riconosco senza AI), con ricerca. /edit_db per correggere o eliminare un alimento.
- /diary — torna in qualsiasi momento alla modalità normale (foto/testo).
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    ensure_user(user.id, chat.id)
    await update.message.reply_text(RECAP_MESSAGE, parse_mode="Markdown")
