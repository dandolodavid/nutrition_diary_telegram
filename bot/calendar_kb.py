import calendar as _calendar
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

MONTHS_IT = [
    "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre",
]
_WEEKDAYS_IT = ["Lu", "Ma", "Me", "Gi", "Ve", "Sa", "Do"]


def build_calendar_keyboard(year: int, month: int, prefix: str) -> InlineKeyboardMarkup:
    """Tastiera inline con un calendario mensile.

    callback_data usa `prefix` per non entrare in conflitto con altri calendari attivi
    nello stesso chat: '{prefix}:nav:YYYY-MM' per cambiare mese, '{prefix}:day:YYYY-MM-DD'
    per scegliere un giorno, '{prefix}:ignore' per titolo/celle vuote (nessun effetto).
    """
    rows = []

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    rows.append([
        InlineKeyboardButton("◀", callback_data=f"{prefix}:nav:{prev_year:04d}-{prev_month:02d}"),
        InlineKeyboardButton(f"{MONTHS_IT[month - 1]} {year}", callback_data=f"{prefix}:ignore"),
        InlineKeyboardButton("▶", callback_data=f"{prefix}:nav:{next_year:04d}-{next_month:02d}"),
    ])
    rows.append([InlineKeyboardButton(wd, callback_data=f"{prefix}:ignore") for wd in _WEEKDAYS_IT])

    today = date.today()
    for week in _calendar.Calendar(firstweekday=0).monthdayscalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data=f"{prefix}:ignore"))
                continue
            label = f"[{day}]" if (year, month, day) == (today.year, today.month, today.day) else str(day)
            row.append(
                InlineKeyboardButton(
                    label, callback_data=f"{prefix}:day:{year:04d}-{month:02d}-{day:02d}"
                )
            )
        rows.append(row)

    return InlineKeyboardMarkup(rows)


def parse_calendar_callback(data: str, prefix: str):
    """Ritorna ('nav', (year, month)), ('day', date) oppure ('ignore', None)."""
    _, kind, *rest = data.split(":")
    if kind == "ignore":
        return "ignore", None
    if kind == "nav":
        year_str, month_str = rest[0].split("-")
        return "nav", (int(year_str), int(month_str))
    if kind == "day":
        return "day", date.fromisoformat(rest[0])
    raise ValueError(f"callback calendario sconosciuto: {data}")
