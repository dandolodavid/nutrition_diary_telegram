from agno.db.postgres import PostgresDb
from pydantic import BaseModel

from config import DATABASE_URL, GEMINI_MODEL_PRIORITY

# Un'unica istanza condivisa: Agno vi crea/gestisce da solo le tabelle di
# sessione e memoria (schema separato da quelle applicative in db/schema.sql).
agno_db = PostgresDb(db_url=DATABASE_URL)


class QuotaExhaustedError(RuntimeError):
    """Tutti i modelli della catena hanno esaurito la quota giornaliera gratuita di Gemini
    (RESOURCE_EXHAUSTED / 429 con quotaId *-FreeTier): non e' un intoppo momentaneo, torna
    a funzionare solo al reset giornaliero di Google, ritentare tra un minuto non serve."""

    retryable = False


def run_with_model_chain(agents: list, *args, is_valid=None, **kwargs):
    """Prova ogni agente (stesso comportamento, modello diverso) nell'ordine dato.

    Passa al successivo su qualsiasi errore (rate limit, modello non disponibile,
    503, ecc.), cosi' un modello deprecato o momentaneamente sovraccarico non blocca
    la richiesta. NB: Agno a volte non solleva un'eccezione ma restituisce l'errore
    come stringa in response.content, quindi controlliamo anche che il contenuto sia
    valido secondo `is_valid` (default: e' un output_schema strutturato, per gli agenti
    con output_schema). Se falliscono tutti, rilancia l'ultimo errore (QuotaExhaustedError
    se anche solo uno dei fallimenti era quota giornaliera esaurita, dato che in quel caso
    nessun ritentativo ravvicinato puo' aiutare).
    """
    if is_valid is None:
        is_valid = lambda content: isinstance(content, BaseModel)  # noqa: E731

    last_error: Exception | None = None
    quota_exhausted = False
    for agent in agents:
        try:
            result = agent.run(*args, **kwargs)
        except Exception as e:
            last_error = e
            if "RESOURCE_EXHAUSTED" in str(e):
                quota_exhausted = True
            continue
        if is_valid(result.content):
            return result
        last_error = RuntimeError(
            f"Risposta non valida dal modello '{agent.model.id}': {result.content!r}"
        )
        if "RESOURCE_EXHAUSTED" in str(last_error):
            quota_exhausted = True

    if quota_exhausted:
        raise QuotaExhaustedError(str(last_error)) from last_error
    raise last_error


__all__ = ["QuotaExhaustedError", "agno_db", "run_with_model_chain", "GEMINI_MODEL_PRIORITY"]
