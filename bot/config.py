import os

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

# Chiave dedicata solo a questo progetto (non piu' condivisa con altre app sul server).
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# Ordine di preferenza dei modelli Gemini: si usa il primo disponibile, con fallback
# automatico ai successivi se uno non risponde (rate limit, 503, deprecato, ecc.).
GEMINI_MODEL_PRIORITY = [
    m.strip()
    for m in os.environ.get(
        "GEMINI_MODEL_PRIORITY",
        # gemini-2.5-flash e gemini-2.5-flash-lite sono stati ritirati (404 per i nuovi
        # utenti): niente fallback morti in coda. Ogni modello ha una quota giornaliera
        # gratuita separata, quindi piu' modelli distinti = piu' resilienza a quota/503.
        "gemini-3.6-flash,gemini-3.8-flash,gemini-3.7-flash,gemini-3.5-flash,"
        "gemini-3.1-flash-lite,gemini-3.5-flash-lite",
    ).split(",")
    if m.strip()
]

DATABASE_URL = os.environ["DATABASE_URL"]

# psycopg3 connessioni dirette (repository.py) vogliono lo schema "postgresql://",
# mentre Agno/SQLAlchemy usano "postgresql+psycopg://". Deriviamo il primo dal secondo
# per non dover configurare due env var separate.
PSYCOPG_DSN = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")
