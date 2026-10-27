"""Конфигурация приложения."""
from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # flight-advisor/
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "flight_advisor.db"

CURRENCY = "RUB"
LANGUAGE = "ru"

# Кэш: сколько часов считать снапшот «свежим» (не перезапрашивать Google).
CACHE_TTL_HOURS = 24

# Маршруты для фонового поллера (накопление истории цен).
# Формат: (origin, dest, dep_start, dep_end) — диапазон дат вылета включительно.
TRACKED_ROUTES: list[tuple[str, str, str, str]] = [
    ("MOW", "TYO", "2027-03-20", "2027-03-26"),  # целевой кейс пользователя
    ("MOW", "IST", "2027-03-20", "2027-03-26"),  # демо-маршрут
    ("MOW", "DXB", "2027-03-20", "2027-03-26"),  # демо-маршрут
]

# Поллер: период цикла и пауза между запросами к Google (вежливость/анти-бан).
POLL_INTERVAL_HOURS = 4
POLL_REQUEST_PAUSE_SEC = 4

# Новости (Guardian + TASS): кэш, объём дайджеста, свежесть айтемов.
# 14 дней, а не 7: ленты по «тихим» странам (Турция и т.п.) низкочастотные —
# при 7 днях дайджест пустеет.
NEWS_TTL_HOURS = 1
NEWS_MAX_ITEMS = 5
NEWS_MAX_AGE_DAYS = 14
