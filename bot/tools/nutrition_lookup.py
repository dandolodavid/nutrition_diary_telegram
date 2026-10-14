import difflib
import json
import re
from functools import lru_cache
from pathlib import Path

import httpx

INDEX_PATH = Path(__file__).parent.parent / "assets" / "alimenti_index.json"
DETAIL_URL = "https://www.alimentinutrizione.it/tabelle-nutrizionali/{food_id}"

STOPWORDS = {
    "alla", "al", "allo", "ai", "agli", "alle", "di", "del", "della", "dei", "delle",
    "con", "e", "il", "la", "lo", "gli", "le", "un", "una", "uno", "in", "da", "per",
}

# Penalizza varianti "conservate" quando l'utente non le ha chieste esplicitamente
# (altrimenti "mele" puo' matchare "Mele, disidratate" invece di quelle fresche).
PENALIZED_TERMS = [
    "disidratat", "liofilizzat", "essiccat", "secco", "secche", "secchi",
    "affumicat", "sciroppat", "in scatola", "sott olio", "sotto sale", "candit",
]
COOKED_TERMS = [
    "griglia", "grigliat", "ai ferri", "cotto", "cotta", "cotti", "cotte",
    "forno", "padella", "bollit", "lesso", "arrosto", "vapore",
]
RAW_TERMS = ["fresc", "crud"]

NUTRIENT_LABELS = {
    "kcal": "Energia (kcal)",
    "protein_g": "Proteine (g)",
    "fat_g": "Lipidi (g)",
    "carbs_g": "Carboidrati disponibili (g)",
}

with open(INDEX_PATH, encoding="utf-8") as f:
    _FOOD_INDEX: dict[str, str] = json.load(f)

_NAMES_NORMALIZED = {}


def _normalize(text: str) -> str:
    text = text.lower().replace("'", " ")
    text = re.sub(r"[^a-zàèéìòù\s]", " ", text)
    return " ".join(text.split())


def _word_variants(word: str) -> set[str]:
    variants = {word}
    if word.endswith("a"):
        variants.add(word[:-1] + "e")
    if word.endswith("o"):
        variants.add(word[:-1] + "i")
    if word.endswith("e"):
        variants.add(word[:-1] + "i")
    return variants


def _search_best_match(query: str) -> tuple[str, str] | None:
    """Ritorna (food_id, nome) del miglior alimento nell'indice locale, o None."""
    if not _NAMES_NORMALIZED:
        _NAMES_NORMALIZED.update({fid: _normalize(name) for fid, name in _FOOD_INDEX.items()})

    q_normalized = _normalize(query)
    q_tokens_all = q_normalized.split()
    q_tokens = [t for t in q_tokens_all if t not in STOPWORDS and len(t) > 2]
    q_variants: set[str] = set()
    for token in q_tokens:
        q_variants |= _word_variants(token)

    if not q_variants:
        return None

    q_wants_cooked = any(term in q_normalized for term in COOKED_TERMS)
    q_wants_penalized = any(term in q_normalized for term in PENALIZED_TERMS)
    q_clean = " ".join(t for t in q_tokens_all if t not in STOPWORDS)

    best_score, best_id = float("-inf"), None
    for food_id, name_normalized in _NAMES_NORMALIZED.items():
        name_tokens_all = name_normalized.split()
        name_tokens = {t for t in name_tokens_all if t not in STOPWORDS}
        overlap = len(q_variants & name_tokens)
        if overlap == 0:
            continue

        name_clean = " ".join(t for t in name_tokens_all if t not in STOPWORDS)
        ratio = difflib.SequenceMatcher(None, q_clean, name_clean).ratio()
        score = overlap * 2 + ratio

        if not q_wants_penalized and any(term in name_normalized for term in PENALIZED_TERMS):
            score -= 3
        if q_wants_cooked and any(term in name_normalized for term in COOKED_TERMS):
            score += 0.5
        elif not q_wants_cooked and any(term in name_normalized for term in RAW_TERMS):
            score += 0.3

        if score > best_score:
            best_score, best_id = score, food_id

    if best_id is None or best_score <= 0:
        return None
    return best_id, _FOOD_INDEX[best_id]


@lru_cache(maxsize=256)
def _fetch_per_100g(food_id: str) -> dict[str, float] | None:
    try:
        response = httpx.get(
            DETAIL_URL.format(food_id=food_id),
            headers={"User-Agent": "Mozilla/5.0 (NutritionDiaryBot)"},
            timeout=10.0,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    html = response.text
    values = {}
    for key, label in NUTRIENT_LABELS.items():
        pattern = re.compile(
            r'<td width="250">' + re.escape(label) + r'</td><td>[^<]*</td><td>\s*([\d.]+|tr)'
        )
        match = pattern.search(html)
        if not match:
            return None
        raw = match.group(1)
        values[key] = 0.0 if raw == "tr" else float(raw)
    return values


def cerca_alimento_it(query: str) -> str:
    """Cerca un alimento nel database ufficiale italiano CREA (alimentinutrizione.it) e
    restituisce i valori nutrizionali per 100g (kcal, proteine, carboidrati, grassi).

    Usa questo strumento per ottenere un dato piu' preciso della tua sola conoscenza, per
    alimenti semplici e generici (es. "pollo", "pane integrale", "mele", "riso basmati",
    "olio di oliva"). Non funziona per prodotti di marca specifici (es. nomi commerciali).
    Per un piatto composito (es. una ricetta con piu' ingredienti), chiama questo strumento
    separatamente per ciascun ingrediente principale e poi combina tu i risultati in base
    alle quantita' indicate dall'utente.

    Args:
        query: nome dell'alimento in italiano, in forma semplice (es. "pollo", "pane",
            "carote"). Per frutta e verdura prova anche la forma plurale se il singolare
            non da' risultati (es. "mele" invece di "mela").

    Returns:
        Una stringa con nome esatto dell'alimento trovato e valori per 100g, oppure un
        messaggio che indica che non e' stato trovato nulla di adatto.
    """
    match = _search_best_match(query)
    if match is None:
        return f"Nessun alimento trovato nel database per '{query}'. Stima con le tue conoscenze."

    food_id, name = match
    values = _fetch_per_100g(food_id)
    if values is None:
        return (
            f"Trovato '{name}' ma non sono riuscito a leggere i valori nutrizionali "
            "(problema di rete momentaneo). Stima con le tue conoscenze."
        )

    return (
        f"Alimento trovato: {name} (fonte: CREA/alimentinutrizione.it). "
        f"Valori per 100g: {values['kcal']:.0f} kcal, {values['protein_g']:.1f}g proteine, "
        f"{values['carbs_g']:.1f}g carboidrati, {values['fat_g']:.1f}g grassi. "
        "Adatta questi valori alla quantita' e al metodo di cottura descritti dall'utente."
    )
