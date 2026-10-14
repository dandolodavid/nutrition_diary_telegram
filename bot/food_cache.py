"""Cache deterministica prima dell'LLM: database personale di alimenti con valori al grammo.

Un messaggio di testo viene spezzato in segmenti "alimento + grammi"; quelli il cui nome
corrisponde a un alimento gia' noto si calcolano a mano (valori_per_g * grammi), solo
gli altri vanno all'LLM. Il database si riempie da solo: dallo storico (backfill una tantum)
e da cio' che l'LLM stima quando l'utente ha dichiarato i grammi.
"""
import logging
import re
import unicodedata
from dataclasses import dataclass, field

from db import repository
from models.schemas import FoodItem

logger = logging.getLogger(__name__)

# Parole che non identificano l'alimento (articoli, preposizioni, "ho mangiato", ...).
STOPWORDS = {
    "a", "e", "ed", "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "di", "del",
    "dello", "della", "dei", "degli", "delle", "da", "in", "con", "al", "allo", "alla", "ai",
    "agli", "alle", "per", "su", "ho", "mangiato", "mangiati", "mangiata", "preso", "presa",
    "bevuto", "circa", "poi", "anche",
}

# Parole che lo stemming confonderebbe ("pesca" frutto / "pesce" animale).
NO_STEM = {"pesca", "pesce"}
# Sotto questa lunghezza (dopo lo stemming) le parole devono essere identiche: un refuso su
# "pollo"/"polpo" cambierebbe alimento.
FUZZY_MIN_LEN = 5
# Se il secondo miglior candidato e' cosi' vicino al primo il match e' ambiguo: meglio l'LLM.
AMBIGUITY_MARGIN = 0.03

_QTY_RE = re.compile(
    r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*(kg|chili|chilo|grammi|grammo|gramm|gr|g)\b", re.IGNORECASE
)
_UNIT_COUNT_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:pezz\w*|fett\w*|stecc\w*|porzion\w*|pallin\w*|cucchiai\w*)\b",
    re.IGNORECASE,
)


@dataclass
class Segment:
    text: str
    grams: float | None = None
    key: str = ""
    tokens: list[str] = field(default_factory=list)


@dataclass
class Resolution:
    items: list[FoodItem]           # voci calcolate dal database
    llm_prompt: str | None          # cosa resta da chiedere all'LLM (None = niente)
    unmatched: list[Segment]        # segmenti ben formati ma sconosciuti (per imparare alias)


# ---------------------------------------------------------------- normalizzazione nomi

def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.replace("'", "").replace("’", "")


def _stem(token: str) -> str:
    # "mela"/"mele", "pomodoro"/"pomodori": si toglie la vocale finale se la parola e' lunga.
    if token in NO_STEM:
        return token
    return token[:-1] if len(token) > 3 and token[-1] in "aeio" else token


def name_tokens(text: str) -> list[str]:
    # Solo lettere: numeri e "%" (es. "0%") non fanno parte dell'identita' dell'alimento.
    return [_stem(t) for t in re.findall(r"[a-z]+", fold(text)) if t not in STOPWORDS]


def name_key(text: str) -> str:
    return " ".join(sorted(name_tokens(text)))


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein con trasposizioni di lettere adiacenti ("yougrt" -> "yogurt" = 1)."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def _similar(a: str, b: str) -> float:
    """1.0 se identiche; con un refuso plausibile un valore < 1; 0 se sono parole diverse."""
    if a == b:
        return 1.0
    longest = max(len(a), len(b))
    if min(len(a), len(b)) < FUZZY_MIN_LEN:
        return 0.0
    tolerance = 1 if longest <= 7 else 2
    distance = _edit_distance(a, b)
    return 1 - distance / longest if distance <= tolerance else 0.0


def _score(query: list[str], candidate: list[str]) -> float:
    """0 se non corrispondono, altrimenti la somiglianza media (1.0 = identici).
    Stesso numero di parole e ognuna deve somigliare a una distinta dell'altro nome:
    cosi' "yogurt greco" non matcha "yogurt greco fragola"."""
    if len(query) != len(candidate) or not query:
        return 0.0
    remaining = list(candidate)
    total = 0.0
    for token in sorted(query, key=len, reverse=True):
        best = max(remaining, key=lambda c: _similar(token, c))
        s = _similar(token, best)
        if s == 0.0:
            return 0.0
        remaining.remove(best)
        total += s
    return total / len(query)


def find_food(foods: list[dict], tokens: list[str]) -> dict | None:
    """L'alimento che corrisponde ai token del nome, o None se nessuno o se ambiguo."""
    if not tokens:
        return None
    key = " ".join(sorted(tokens))
    scored: list[tuple[float, dict]] = []
    for food in foods:
        keys = [food["name_key"], *food["aliases"]]
        if key in keys:
            return food
        best = max((_score(tokens, k.split()) for k in keys), default=0.0)
        if best > 0:
            scored.append((best, food))
    if not scored:
        return None
    scored.sort(key=lambda sf: sf[0], reverse=True)
    if len(scored) > 1 and scored[0][0] - scored[1][0] < AMBIGUITY_MARGIN:
        return None
    return scored[0][1]


# ---------------------------------------------------------------- parsing del messaggio

def _grams(match: re.Match) -> float:
    value = float(match.group(1).replace(",", "."))
    return value * 1000 if match.group(2).lower() in ("kg", "chili", "chilo") else value


def _quantities(text: str) -> list[re.Match]:
    return list(_QTY_RE.finditer(text))


def has_quantity(text: str) -> bool:
    return bool(_QTY_RE.search(text))


def _make_segment(text: str) -> Segment:
    quantities = _quantities(text)
    if len(quantities) != 1:
        return Segment(text)
    m = quantities[0]
    tokens = name_tokens(text[:m.start()] + " " + text[m.end():])
    if not tokens:
        return Segment(text)
    return Segment(text, _grams(m), " ".join(sorted(tokens)), tokens)


def split_segments(text: str) -> list[Segment]:
    # La virgola separa gli alimenti, ma non e' un separatore dentro un decimale ("87,5 g").
    parts = [p.strip() for p in re.split(r"[\n;+]|(?<!\d),|,(?!\d)", text) if p.strip()]

    # "yogurt greco, 250 g": la virgola separa nome e quantita' dello stesso alimento.
    merged: list[str] = []
    for part in parts:
        qty_only = _quantities(part) and not name_tokens(_QTY_RE.sub(" ", part))
        if qty_only and merged and not _quantities(merged[-1]):
            merged[-1] += " " + part
        else:
            merged.append(part)

    segments: list[Segment] = []
    for part in merged:
        if len(_quantities(part)) > 1:
            # "250g yogurt e 50g cereali": si separa su " e " solo se c'e' piu' di una quantita'.
            pieces = [p.strip() for p in re.split(r"\s+e\s+", part) if p.strip()]
            segments.extend(_make_segment(p) for p in pieces)
        else:
            segments.append(_make_segment(part))
    return segments


def _fmt_grams(grams: float) -> str:
    return f"{grams:g}"


def _item_from_food(food: dict, grams: float) -> FoodItem:
    return FoodItem(
        name=f"{food['name']}, {_fmt_grams(grams)}g",
        kcal=round(food["kcal_per_g"] * grams, 1),
        protein_g=round(food["protein_per_g"] * grams, 1),
        carbs_g=round(food["carbs_per_g"] * grams, 1),
        fat_g=round(food["fat_per_g"] * grams, 1),
    )


def resolve_message(user_id: int, text: str) -> Resolution:
    """Calcola dal database gli alimenti noti del messaggio; il resto va all'LLM.
    Se non si riconosce nulla, l'LLM riceve il testo originale invariato."""
    foods = repository.get_foods(user_id)
    if not foods or not text:
        return Resolution([], text, [])

    items: list[FoodItem] = []
    leftover: list[Segment] = []
    for segment in split_segments(text):
        food = find_food(foods, segment.tokens) if segment.grams else None
        if food is not None:
            items.append(_item_from_food(food, segment.grams))
        else:
            leftover.append(segment)

    if not items:
        return Resolution([], text, [s for s in leftover if s.grams])
    if not leftover:
        return Resolution(items, None, [])
    return Resolution(items, "\n".join(s.text for s in leftover), [s for s in leftover if s.grams])


# ---------------------------------------------------------------- apprendimento

def split_name_grams(name: str) -> tuple[str, float] | None:
    """Da "Yogurt greco, 250g" o "Castagne, 5 pezzi (40g)" a ("Yogurt greco", 250.0).
    None se il nome non e' una voce semplice con un'unica quantita' in grammi."""
    quantities = _quantities(name)
    if len(quantities) != 1:
        return None
    m = quantities[0]
    base = name[:m.start()] + " " + name[m.end():]
    base = re.sub(r"\(\s*\)", " ", base)
    base = _UNIT_COUNT_RE.sub(" ", base)
    base = re.sub(r"\s+", " ", base).strip(" ,;-()")
    if not name_tokens(base) or any(c in base for c in "(),"):
        return None
    grams = _grams(m)
    return (base, grams) if grams > 0 else None


def _food_args(item: FoodItem, base: str, grams: float) -> dict:
    return {
        "name": base,
        "name_key": name_key(base),
        "kcal_per_g": item.kcal / grams,
        "protein_per_g": item.protein_g / grams,
        "carbs_per_g": item.carbs_g / grams,
        "fat_per_g": item.fat_g / grams,
    }


def learn_from_llm(user_id: int, items: list[FoodItem], prompt: str,
                   unmatched: list[Segment] | None = None) -> None:
    """Aggiunge al database le voci stimate dall'LLM, ma solo se i grammi sono quelli che
    l'utente ha scritto (altrimenti il per-grammo sarebbe basato su una quantita' inventata).
    Se i segmenti sconosciuti si abbinano uno a uno alle voci (stessi grammi, stesso ordine)
    il modo in cui l'utente ha scritto l'alimento diventa un alias, cosi' al prossimo
    messaggio uguale il match e' immediato."""
    declared = {_grams(m) for m in _quantities(prompt)}
    parsed = [(item, split_name_grams(item.name)) for item in items]

    segments = [s for s in (unmatched or []) if s.grams]
    aliases: dict[int, str] = {}
    if (segments and len(segments) == len(items)
            and len({s.grams for s in segments}) == len(segments)):
        for index, (segment, (_, name_grams)) in enumerate(zip(segments, parsed)):
            if name_grams and abs(name_grams[1] - segment.grams) < 0.01:
                aliases[index] = segment.key

    for index, (item, name_grams) in enumerate(parsed):
        if name_grams is None:
            continue
        base, grams = name_grams
        if not any(abs(grams - d) < 0.01 for d in declared):
            continue
        args = _food_args(item, base, grams)
        alias = aliases.get(index)
        repository.upsert_food(
            user_id, source="llm",
            aliases=[alias] if alias and alias != args["name_key"] else [], **args,
        )


def backfill_from_history(user_id: int) -> int:
    """Costruisce il database dallo storico dei pasti a testo/voce (l'ultimo valore per ogni
    alimento vince). Ritorna quanti alimenti distinti ha creato."""
    keys = set()
    for row in repository.get_text_items_history(user_id):
        name_grams = split_name_grams(row["name"])
        if name_grams is None:
            continue
        base, grams = name_grams
        item = FoodItem(name=row["name"], kcal=row["kcal"], protein_g=row["protein_g"],
                        carbs_g=row["carbs_g"], fat_g=row["fat_g"])
        args = _food_args(item, base, grams)
        repository.upsert_food(user_id, source="history", overwrite=True, **args)
        keys.add(args["name_key"])
    return len(keys)


def bootstrap() -> None:
    """All'avvio: crea le tabelle mancanti e popola il database di chi non ne ha ancora."""
    repository.init_schema()
    for user_id in repository.get_users_without_foods():
        try:
            created = backfill_from_history(user_id)
        except Exception:
            # Un backfill rotto non deve impedire l'avvio: il database si riempie comunque
            # da solo con i nuovi pasti.
            logger.exception("Backfill del database alimenti fallito per l'utente %s", user_id)
            continue
        logger.info("Database alimenti: %s alimenti creati dallo storico dell'utente %s",
                    created, user_id)
