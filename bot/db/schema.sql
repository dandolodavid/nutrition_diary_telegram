CREATE TABLE IF NOT EXISTS users (
    telegram_user_id  BIGINT PRIMARY KEY,
    chat_id           BIGINT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS targets (
    telegram_user_id  BIGINT PRIMARY KEY REFERENCES users(telegram_user_id),
    kcal              NUMERIC NOT NULL,
    protein_g         NUMERIC NOT NULL,
    carbs_g           NUMERIC NOT NULL,
    fat_g             NUMERIC NOT NULL,
    source            TEXT NOT NULL CHECK (source IN ('manual', 'diet_pdf')),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS diary_entries (
    id                BIGSERIAL PRIMARY KEY,
    telegram_user_id  BIGINT NOT NULL REFERENCES users(telegram_user_id),
    logged_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    description       TEXT NOT NULL,
    kcal              NUMERIC NOT NULL,
    protein_g         NUMERIC NOT NULL,
    carbs_g           NUMERIC NOT NULL,
    fat_g             NUMERIC NOT NULL,
    source_type       TEXT NOT NULL CHECK (source_type IN ('photo', 'text', 'voice'))
);
CREATE INDEX IF NOT EXISTS idx_diary_entries_user_time ON diary_entries (telegram_user_id, logged_at);

CREATE TABLE IF NOT EXISTS diary_items (
    id                BIGSERIAL PRIMARY KEY,
    diary_entry_id    BIGINT NOT NULL REFERENCES diary_entries(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    kcal              NUMERIC NOT NULL,
    protein_g         NUMERIC NOT NULL,
    carbs_g           NUMERIC NOT NULL,
    fat_g             NUMERIC NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_diary_items_entry ON diary_items (diary_entry_id);

CREATE TABLE IF NOT EXISTS weight_entries (
    id                BIGSERIAL PRIMARY KEY,
    telegram_user_id  BIGINT NOT NULL REFERENCES users(telegram_user_id),
    log_date          DATE NOT NULL,
    weight_kg         NUMERIC NOT NULL,
    logged_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (telegram_user_id, log_date)
);
CREATE INDEX IF NOT EXISTS idx_weight_entries_user_date ON weight_entries (telegram_user_id, log_date);

-- Cache deterministica pre-LLM: valori nutrizionali PER GRAMMO di alimenti gia' noti.
-- name_key = token normalizzati del nome, ordinati (vedi food_cache.name_key);
-- aliases = altre chiavi con cui l'utente ha scritto lo stesso alimento.
CREATE TABLE IF NOT EXISTS foods (
    id                BIGSERIAL PRIMARY KEY,
    telegram_user_id  BIGINT NOT NULL REFERENCES users(telegram_user_id),
    name              TEXT NOT NULL,
    name_key          TEXT NOT NULL,
    aliases           TEXT[] NOT NULL DEFAULT '{}',
    kcal_per_g        NUMERIC NOT NULL,
    protein_per_g     NUMERIC NOT NULL,
    carbs_per_g       NUMERIC NOT NULL,
    fat_per_g         NUMERIC NOT NULL,
    source            TEXT NOT NULL CHECK (source IN ('llm', 'history', 'manual')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (telegram_user_id, name_key)
);
