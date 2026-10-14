import asyncio
import logging
import time

from agno.media import Image
from telegram import Update
from telegram.ext import ContextTypes

from agents._shared import QuotaExhaustedError
from agents.nutrition_agent import run_nutrition_agent
from agents.transcription_agent import transcribe_audio
from db.repository import add_diary_entry, ensure_user
from food_cache import Resolution, learn_from_llm, resolve_message
from models.schemas import FoodItem, NutritionEstimate
from retry import RETRY_DELAY_SECONDS, run_with_retry

logger = logging.getLogger(__name__)

# Se l'LLM chiede un chiarimento su una parte del messaggio, gli alimenti gia' calcolati
# dal database restano in sospeso per questo tempo e vengono salvati con la risposta.
PENDING_TTL_SECONDS = 15 * 60
PENDING_KEY = "pending_foods"

DIARY_INTRO = (
    "Modalita diario attiva: mandami una foto del piatto, scrivimi o mandami un vocale "
    "con cosa hai mangiato."
)


async def diary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(DIARY_INTRO)


def _totals(estimate: NutritionEstimate) -> dict:
    return {
        "kcal": sum(item.kcal for item in estimate.items),
        "protein_g": sum(item.protein_g for item in estimate.items),
        "carbs_g": sum(item.carbs_g for item in estimate.items),
        "fat_g": sum(item.fat_g for item in estimate.items),
    }


def _pending_items(context: ContextTypes.DEFAULT_TYPE) -> list[FoodItem]:
    pending = context.user_data.get(PENDING_KEY)
    if not pending or time.time() - pending["ts"] > PENDING_TTL_SECONDS:
        context.user_data.pop(PENDING_KEY, None)
        return []
    return [FoodItem(**item) for item in pending["items"]]


def _format_summary(estimate: NutritionEstimate, totals: dict, from_db: int = 0,
                    carried: int = 0) -> str:
    lines = ["🍽️ Riepilogo:"]
    for item in estimate.items:
        lines.append(
            f"- {item.name}: {item.kcal:.0f} kcal, {item.protein_g:.0f}g proteine, "
            f"{item.carbs_g:.0f}g carboidrati, {item.fat_g:.0f}g grassi"
        )
    lines.append("")
    lines.append(
        f"Totale: {totals['kcal']:.0f} kcal | {totals['protein_g']:.0f}g proteine | "
        f"{totals['carbs_g']:.0f}g carboidrati | {totals['fat_g']:.0f}g grassi"
    )
    lines.append("")
    if from_db:
        lines.append(f"📚 {from_db} alimenti calcolati dal tuo database (senza AI)")
    if carried:
        lines.append(
            f"↩️ Inclusi {carried} alimenti del messaggio precedente, rimasti in sospeso "
            "durante il chiarimento"
        )
    lines.append("Salvato nel diario ✅")
    return "\n".join(lines)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    ensure_user(user.id, chat.id)

    images = None
    prompt = None
    voice_or_audio = update.message.voice or update.message.audio

    if update.message.photo:
        source_type = "photo"
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = bytes(await file.download_as_bytearray())
        images = [Image(content=photo_bytes)]
        prompt = update.message.caption or "Analizza il piatto in foto."
    elif voice_or_audio:
        source_type = "voice"
    else:
        source_type = "text"
        prompt = update.message.text

    await context.bot.send_chat_action(chat_id=chat.id, action="typing")

    async def attempt_estimate() -> None:
        nonlocal prompt

        if source_type == "voice" and prompt is None:
            file = await context.bot.get_file(voice_or_audio.file_id)
            audio_bytes = bytes(await file.download_as_bytearray())
            mime_type = getattr(voice_or_audio, "mime_type", None) or "audio/ogg"
            transcript = await asyncio.to_thread(transcribe_audio, audio_bytes, mime_type)
            if transcript.startswith("Non sono riuscito a capire l'audio"):
                raise RuntimeError(transcript)
            await context.bot.send_message(chat.id, f'🎙️ Ho capito: "{transcript}"')
            prompt = transcript

        # Prima dell'LLM: gli alimenti gia' noti (nome + grammi) si calcolano dal database.
        # Le foto vanno sempre all'LLM; per testo/voce vi arriva solo cio' che non si conosce.
        resolution = Resolution([], prompt, [])
        if images is None and prompt:
            resolution = await asyncio.to_thread(resolve_message, user.id, prompt)
        carried = _pending_items(context)
        known_items = carried + resolution.items

        llm_items: list[FoodItem] = []
        if resolution.llm_prompt is None:
            estimate = NutritionEstimate(clarification_needed=False, items=known_items)
        else:
            response = await asyncio.to_thread(
                run_nutrition_agent,
                resolution.llm_prompt,
                images=images,
                user_id=str(user.id),
                session_id=str(chat.id),
            )
            estimate = response.content
            if not isinstance(estimate, NutritionEstimate):
                raise RuntimeError(f"Risposta non strutturata dall'agente: {estimate!r}")

            if estimate.clarification_needed:
                if known_items:
                    context.user_data[PENDING_KEY] = {
                        "ts": time.time(),
                        "items": [item.model_dump() for item in known_items],
                    }
                await context.bot.send_message(chat.id, estimate.clarification_question)
                return

            if not estimate.items:
                # Risposta degenere (es. l'agente si e' confuso usando uno strumento e non ha
                # prodotto voci reali): trattarla come un fallimento, non salvarla nel diario.
                raise RuntimeError(f"Stima senza alimenti dall'agente: {estimate!r}")

            llm_items = estimate.items
            estimate = NutritionEstimate(
                clarification_needed=False, items=known_items + llm_items
            )

        totals = _totals(estimate)
        description = ", ".join(item.name for item in estimate.items)
        items = [
            {
                "name": item.name,
                "kcal": item.kcal,
                "protein_g": item.protein_g,
                "carbs_g": item.carbs_g,
                "fat_g": item.fat_g,
            }
            for item in estimate.items
        ]

        add_diary_entry(
            telegram_user_id=user.id,
            description=description,
            items=items,
            kcal=totals["kcal"],
            protein_g=totals["protein_g"],
            carbs_g=totals["carbs_g"],
            fat_g=totals["fat_g"],
            source_type=source_type,
        )
        context.user_data.pop(PENDING_KEY, None)
        await context.bot.send_message(
            chat.id,
            _format_summary(estimate, totals, from_db=len(resolution.items),
                            carried=len(carried)),
        )

        if llm_items and images is None:
            try:
                await asyncio.to_thread(
                    learn_from_llm, user.id, llm_items, resolution.llm_prompt,
                    resolution.unmatched,
                )
            except Exception:
                # Imparare e' un di piu': il pasto e' gia' salvato, non va fatto fallire.
                logger.exception("Aggiornamento del database alimenti fallito")

    async def on_first_failure() -> None:
        await context.bot.send_message(
            chat.id,
            "⏳ L'assistente AI è momentaneamente occupato (troppe richieste ravvicinate). "
            f"Ci riprovo automaticamente tra circa {RETRY_DELAY_SECONDS} secondi, non serve "
            "rimandare il messaggio.",
        )

    async def on_give_up(error: Exception) -> None:
        if isinstance(error, QuotaExhaustedError):
            await context.bot.send_message(
                chat.id,
                "🚫 Ho esaurito la quota giornaliera gratuita di Gemini: non e' un intoppo "
                "temporaneo, ritentare adesso non serve. Torna a funzionare da solo al reset "
                "quota di Google (di solito nel giro di 24h); per usarlo senza questo limite "
                "serve attivare la fatturazione sulla chiave API su Google AI Studio.",
            )
            return
        await context.bot.send_message(
            chat.id,
            "⚠️ Non sono riuscito a completare la stima dopo diversi tentativi. Riprova a "
            "inviare di nuovo il messaggio tra un po'.",
        )

    await run_with_retry(
        context,
        attempt_estimate,
        chat_id=chat.id,
        on_first_failure=on_first_failure,
        on_give_up=on_give_up,
    )
