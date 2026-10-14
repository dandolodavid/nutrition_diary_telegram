from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg

from config import PSYCOPG_DSN


@contextmanager
def _conn():
    with psycopg.connect(PSYCOPG_DSN, autocommit=True) as conn:
        yield conn


@contextmanager
def _txn():
    # autocommit di default e' False: la transazione si chiude con commit in
    # uscita pulita dal blocco `with`, o rollback se viene sollevata un'eccezione.
    with psycopg.connect(PSYCOPG_DSN) as conn:
        yield conn


def ensure_user(telegram_user_id: int, chat_id: int) -> None:
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO users (telegram_user_id, chat_id)
            VALUES (%s, %s)
            ON CONFLICT (telegram_user_id) DO UPDATE SET chat_id = EXCLUDED.chat_id
            """,
            (telegram_user_id, chat_id),
        )


def upsert_targets(telegram_user_id: int, kcal: float, protein_g: float,
                    carbs_g: float, fat_g: float, source: str) -> None:
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO targets (telegram_user_id, kcal, protein_g, carbs_g, fat_g, source, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (telegram_user_id) DO UPDATE SET
                kcal = EXCLUDED.kcal,
                protein_g = EXCLUDED.protein_g,
                carbs_g = EXCLUDED.carbs_g,
                fat_g = EXCLUDED.fat_g,
                source = EXCLUDED.source,
                updated_at = now()
            """,
            (telegram_user_id, kcal, protein_g, carbs_g, fat_g, source),
        )


def get_targets(telegram_user_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT kcal, protein_g, carbs_g, fat_g, source FROM targets WHERE telegram_user_id = %s",
            (telegram_user_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "kcal": float(row[0]),
        "protein_g": float(row[1]),
        "carbs_g": float(row[2]),
        "fat_g": float(row[3]),
        "source": row[4],
    }


def add_diary_entry(telegram_user_id: int, description: str, items: list[dict], kcal: float,
                     protein_g: float, carbs_g: float, fat_g: float, source_type: str) -> None:
    """Salva la riga aggregata del messaggio + una riga per ciascun alimento in items
    (dict con name/kcal/protein_g/carbs_g/fat_g), in un'unica transazione."""
    with _txn() as conn:
        entry_id = conn.execute(
            """
            INSERT INTO diary_entries
                (telegram_user_id, description, kcal, protein_g, carbs_g, fat_g, source_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (telegram_user_id, description, kcal, protein_g, carbs_g, fat_g, source_type),
        ).fetchone()[0]

        for item in items:
            conn.execute(
                """
                INSERT INTO diary_items (diary_entry_id, name, kcal, protein_g, carbs_g, fat_g)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    entry_id,
                    item["name"],
                    item["kcal"],
                    item["protein_g"],
                    item["carbs_g"],
                    item["fat_g"],
                ),
            )


def get_entries_between(telegram_user_id: int, start: datetime, end: datetime) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT id, logged_at, description, kcal, protein_g, carbs_g, fat_g
            FROM diary_entries
            WHERE telegram_user_id = %s AND logged_at >= %s AND logged_at < %s
            ORDER BY logged_at
            """,
            (telegram_user_id, start, end),
        ).fetchall()
    return [
        {
            "id": r[0],
            "logged_at": r[1],
            "description": r[2],
            "kcal": float(r[3]),
            "protein_g": float(r[4]),
            "carbs_g": float(r[5]),
            "fat_g": float(r[6]),
        }
        for r in rows
    ]


def delete_diary_entry(telegram_user_id: int, entry_id: int) -> dict | None:
    """Cancella una entry (e i suoi diary_items a cascata) se appartiene all'utente.
    Ritorna la entry cancellata (description, kcal) o None se non trovata/non sua."""
    with _conn() as conn:
        row = conn.execute(
            """
            DELETE FROM diary_entries WHERE id = %s AND telegram_user_id = %s
            RETURNING description, kcal
            """,
            (entry_id, telegram_user_id),
        ).fetchone()
    if row is None:
        return None
    return {"description": row[0], "kcal": float(row[1])}


def get_items_between(telegram_user_id: int, start: datetime, end: datetime) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT e.logged_at, i.name, i.kcal, i.protein_g, i.carbs_g, i.fat_g
            FROM diary_items i
            JOIN diary_entries e ON e.id = i.diary_entry_id
            WHERE e.telegram_user_id = %s AND e.logged_at >= %s AND e.logged_at < %s
            ORDER BY e.logged_at
            """,
            (telegram_user_id, start, end),
        ).fetchall()
    return [
        {
            "logged_at": r[0],
            "name": r[1],
            "kcal": float(r[2]),
            "protein_g": float(r[3]),
            "carbs_g": float(r[4]),
            "fat_g": float(r[5]),
        }
        for r in rows
    ]


def upsert_weight_entry(telegram_user_id: int, weight_kg: float, log_date: date | None = None) -> None:
    """Salva il peso per un giorno; se esiste gia' una misurazione per quel giorno la sovrascrive."""
    if log_date is None:
        log_date = datetime.now(timezone.utc).astimezone().date()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO weight_entries (telegram_user_id, log_date, weight_kg, logged_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (telegram_user_id, log_date) DO UPDATE SET
                weight_kg = EXCLUDED.weight_kg,
                logged_at = now()
            """,
            (telegram_user_id, log_date, weight_kg),
        )


def get_weight_entries_since(telegram_user_id: int, since_date: date) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT log_date, weight_kg
            FROM weight_entries
            WHERE telegram_user_id = %s AND log_date >= %s
            ORDER BY log_date
            """,
            (telegram_user_id, since_date),
        ).fetchall()
    return [{"log_date": r[0], "weight_kg": float(r[1])} for r in rows]


def period_bounds(period: str, ref_date: date | None = None) -> tuple[datetime, datetime]:
    """Intervallo [start, end) in UTC per il periodo indicato che contiene ref_date
    (default: oggi). Per "day" e' il giorno stesso, per "week" lo lun-dom che lo contiene,
    per "month" il mese di calendario che lo contiene."""
    if ref_date is None:
        ref_date = datetime.now(timezone.utc).date()
    start_of_day = datetime.combine(ref_date, datetime.min.time(), tzinfo=timezone.utc)
    if period == "day":
        return start_of_day, start_of_day + timedelta(days=1)
    if period == "week":
        start = start_of_day - timedelta(days=ref_date.weekday())
        return start, start + timedelta(days=7)
    if period == "month":
        start = start_of_day.replace(day=1)
        end = start.replace(year=start.year + 1, month=1) if start.month == 12 \
            else start.replace(month=start.month + 1)
        return start, end
    raise ValueError(f"periodo sconosciuto: {period}")


def init_schema() -> None:
    """Applica schema.sql (tutto IF NOT EXISTS): serve perche' lo script in
    docker-entrypoint-initdb.d gira solo alla creazione del volume, quindi le tabelle
    aggiunte dopo non esisterebbero su un database gia' esistente."""
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    with _conn() as conn:
        conn.execute(schema)


def _food_row(r) -> dict:
    return {
        "id": r[0],
        "name": r[1],
        "name_key": r[2],
        "aliases": list(r[3]),
        "kcal_per_g": float(r[4]),
        "protein_per_g": float(r[5]),
        "carbs_per_g": float(r[6]),
        "fat_per_g": float(r[7]),
        "source": r[8],
    }


def get_foods(telegram_user_id: int) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT id, name, name_key, aliases, kcal_per_g, protein_per_g, carbs_per_g,
                   fat_per_g, source
            FROM foods WHERE telegram_user_id = %s ORDER BY name
            """,
            (telegram_user_id,),
        ).fetchall()
    return [_food_row(r) for r in rows]


def upsert_food(telegram_user_id: int, name: str, name_key: str, kcal_per_g: float,
                protein_per_g: float, carbs_per_g: float, fat_per_g: float, source: str,
                aliases: list[str] | None = None, overwrite: bool = False) -> None:
    """Inserisce un alimento. Se name_key esiste gia': con overwrite=False i valori restano
    quelli salvati (cosi' i calcoli sono stabili nel tempo) e si aggiungono solo gli alias;
    con overwrite=True i valori vengono sostituiti (usato dal backfill, che scorre lo
    storico in ordine cronologico cosi' l'ultimo valore vince)."""
    values_update = """
                name = EXCLUDED.name,
                kcal_per_g = EXCLUDED.kcal_per_g,
                protein_per_g = EXCLUDED.protein_per_g,
                carbs_per_g = EXCLUDED.carbs_per_g,
                fat_per_g = EXCLUDED.fat_per_g,
                source = EXCLUDED.source,
    """ if overwrite else ""
    with _conn() as conn:
        conn.execute(
            f"""
            INSERT INTO foods (telegram_user_id, name, name_key, aliases, kcal_per_g,
                               protein_per_g, carbs_per_g, fat_per_g, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (telegram_user_id, name_key) DO UPDATE SET
                {values_update}
                aliases = ARRAY(SELECT DISTINCT unnest(foods.aliases || EXCLUDED.aliases))
            """,
            (telegram_user_id, name, name_key, aliases or [], kcal_per_g, protein_per_g,
             carbs_per_g, fat_per_g, source),
        )


def get_users_without_foods() -> list[int]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT u.telegram_user_id FROM users u
            WHERE NOT EXISTS (SELECT 1 FROM foods f WHERE f.telegram_user_id = u.telegram_user_id)
            """
        ).fetchall()
    return [r[0] for r in rows]


def get_text_items_history(telegram_user_id: int) -> list[dict]:
    """Tutte le voci dei pasti scritti/dettati (non da foto: li' i grammi sono una stima
    dell'LLM su un piatto, non un dato dichiarato dall'utente), dalla piu' vecchia."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT i.name, i.kcal, i.protein_g, i.carbs_g, i.fat_g
            FROM diary_items i
            JOIN diary_entries e ON e.id = i.diary_entry_id
            WHERE e.telegram_user_id = %s AND e.source_type IN ('text', 'voice')
            ORDER BY i.id
            """,
            (telegram_user_id,),
        ).fetchall()
    return [
        {"name": r[0], "kcal": float(r[1]), "protein_g": float(r[2]),
         "carbs_g": float(r[3]), "fat_g": float(r[4])}
        for r in rows
    ]


def get_food(telegram_user_id: int, food_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT id, name, name_key, aliases, kcal_per_g, protein_per_g, carbs_per_g,
                   fat_per_g, source
            FROM foods WHERE id = %s AND telegram_user_id = %s
            """,
            (food_id, telegram_user_id),
        ).fetchone()
    return _food_row(row) if row else None


def update_food(telegram_user_id: int, food_id: int, *, name: str | None = None,
                name_key: str | None = None, per_g: tuple[float, float, float, float] | None = None
                ) -> bool:
    """Modifica nome (con la sua chiave) e/o valori per grammo (kcal, proteine, carboidrati,
    grassi) di un alimento dell'utente, e lo marca 'manual'. Solleva UniqueViolation se la
    nuova chiave esiste gia' per un altro alimento. Ritorna False se non esiste."""
    sets, params = ["source = 'manual'"], []
    if name is not None:
        sets += ["name = %s", "name_key = %s"]
        params += [name, name_key]
    if per_g is not None:
        sets += ["kcal_per_g = %s", "protein_per_g = %s", "carbs_per_g = %s", "fat_per_g = %s"]
        params += list(per_g)
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE foods SET {', '.join(sets)} WHERE id = %s AND telegram_user_id = %s",
            (*params, food_id, telegram_user_id),
        )
    return cur.rowcount > 0


def delete_food(telegram_user_id: int, food_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM foods WHERE id = %s AND telegram_user_id = %s",
            (food_id, telegram_user_id),
        )
    return cur.rowcount > 0
