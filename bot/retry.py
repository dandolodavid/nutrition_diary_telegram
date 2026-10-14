import logging
from functools import partial
from typing import Awaitable, Callable, Optional

from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Il free tier di Gemini resetta la quota ogni minuto: 65s da margine di sicurezza.
RETRY_DELAY_SECONDS = 65
MAX_ATTEMPTS = 3  # tentativo iniziale + 2 retry in coda


async def run_with_retry(
    context: ContextTypes.DEFAULT_TYPE,
    task: Callable[[], Awaitable[None]],
    *,
    chat_id: int,
    on_first_failure: Optional[Callable[[], Awaitable[None]]] = None,
    on_give_up: Optional[Callable[[Exception], Awaitable[None]]] = None,
    attempt: int = 1,
) -> None:
    """Esegue `task`; se fallisce lo rimette in coda con JobQueue dopo
    RETRY_DELAY_SECONDS, fino a MAX_ATTEMPTS tentativi totali."""
    try:
        await task()
    except Exception as error:
        logger.exception("Tentativo %s/%s fallito per chat %s", attempt, MAX_ATTEMPTS, chat_id)

        if not getattr(error, "retryable", True):
            # Errore per cui ritentare non cambierebbe nulla (es. quota giornaliera
            # esaurita): niente attesa di RETRY_DELAY_SECONDS, si comunica e basta.
            if on_give_up is not None:
                await on_give_up(error)
            return

        if attempt == 1 and on_first_failure is not None:
            await on_first_failure()

        if attempt >= MAX_ATTEMPTS:
            if on_give_up is not None:
                await on_give_up(error)
            return

        context.job_queue.run_once(
            partial(
                _retry_job,
                task=task,
                chat_id=chat_id,
                on_first_failure=on_first_failure,
                on_give_up=on_give_up,
                attempt=attempt + 1,
            ),
            when=RETRY_DELAY_SECONDS,
        )


async def _retry_job(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    task: Callable[[], Awaitable[None]],
    chat_id: int,
    on_first_failure: Optional[Callable[[], Awaitable[None]]],
    on_give_up: Optional[Callable[[], Awaitable[None]]],
    attempt: int,
) -> None:
    await run_with_retry(
        context,
        task,
        chat_id=chat_id,
        on_first_failure=on_first_failure,
        on_give_up=on_give_up,
        attempt=attempt,
    )
