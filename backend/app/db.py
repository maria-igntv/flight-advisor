"""SQLite: хранение снапшотов цен (для накопления истории + кэша)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from .config import DB_PATH, CACHE_TTL_HOURS, NEWS_TTL_HOURS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL,
    dest TEXT NOT NULL,
    dep_date TEXT NOT NULL,
    return_date TEXT,
    passengers TEXT NOT NULL DEFAULT '1-0-0',
    price INTEGER,
    airlines TEXT,
    currency TEXT,
    fetched_at TEXT NOT NULL,
    cheapest_offer TEXT
);
CREATE INDEX IF NOT EXISTS idx_route_date ON price_snapshots(origin, dest, dep_date);
CREATE TABLE IF NOT EXISTS news_cache (
    region_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""

# Мягкая миграция старой базы: добавляем колонку с деталями лучшего оффера
# и состав пассажиров (старые снапшоты поллера — «1 взрослый»).
_MIGRATIONS = [
    "ALTER TABLE price_snapshots ADD COLUMN cheapest_offer TEXT",
    "ALTER TABLE price_snapshots ADD COLUMN passengers TEXT NOT NULL DEFAULT '1-0-0'",
    # Демо-история за прошлый год (синтетика): помечаем отдельно, чтобы
    # не выдавать её за кэш календаря и не смешивать с реальными измерениями.
    "ALTER TABLE price_snapshots ADD COLUMN synthetic INTEGER NOT NULL DEFAULT 0",
]
_POST_MIGRATE = [
    "UPDATE price_snapshots SET passengers='1-0-0' WHERE passengers IS NULL OR passengers=''",
]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as c:
        c.executescript(_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                pass  # колонка уже есть
        for stmt in _POST_MIGRATE:
            c.execute(stmt)


def save_snapshot(
    origin: str,
    dest: str,
    dep_date: str,
    price: int | None,
    airlines: list[str],
    currency: str,
    return_date: str | None = None,
    cheapest_offer: dict | None = None,
    passengers: str = "1-0-0",
) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO price_snapshots (origin, dest, dep_date, return_date, passengers, price, airlines, currency, fetched_at, cheapest_offer) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                origin,
                dest,
                dep_date,
                return_date,
                passengers,
                price,
                json.dumps(airlines),
                currency,
                datetime.now().isoformat(),
                json.dumps(cheapest_offer, ensure_ascii=False) if cheapest_offer else None,
            ),
        )


def save_demo_snapshot(
    origin: str,
    dest: str,
    dep_date: str,
    price: int,
    airlines: list[str],
    currency: str,
    return_date: str | None = None,
    passengers: str = "1-0-0",
    fetched_at: str | None = None,
) -> None:
    """Снапшот демо-истории: synthetic=1 и ЯВНЫЙ fetched_at («как будто
    собрано» в ту дату) — см. engines/demo_history.py."""
    with _conn() as c:
        c.execute(
            "INSERT INTO price_snapshots (origin, dest, dep_date, return_date, passengers, price, airlines, currency, fetched_at, cheapest_offer, synthetic) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1)",
            (
                origin, dest, dep_date, return_date, passengers, price,
                json.dumps(airlines, ensure_ascii=False), currency,
                fetched_at or datetime.now().isoformat(),
            ),
        )


def synthetic_dates(
    origin: str,
    dest: str,
    dep_date: str,
    return_date: str | None = None,
    passengers: str = "1-0-0",
) -> set[str]:
    """Даты (YYYY-MM-DD) уже созданных демо-снапшотов под ключ поиска."""
    with _conn() as c:
        rows = c.execute(
            "SELECT DISTINCT fetched_at FROM price_snapshots "
            "WHERE origin=? AND dest=? AND dep_date=? AND return_date IS ? "
            "AND passengers IS ? AND synthetic=1",
            (origin, dest, dep_date, return_date, passengers),
        ).fetchall()
    return {r["fetched_at"][:10] for r in rows}


def latest_snapshot(
    origin: str, dest: str, dep_date: str, return_date: str | None = None,
    passengers: str = "1-0-0",
) -> dict | None:
    """Последний снапшот для маршрута+даты (для кэша), если свежий.

    return_date и passengers обязательны в фильтре: one-way/round-trip и
    разный состав пассажиров не смешиваются. Демо-синтетика (synthetic=1)
    кэшем календаря быть не может — только реальными измерениями.
    """
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM price_snapshots WHERE origin=? AND dest=? AND dep_date=? "
            "AND return_date IS ? AND passengers IS ? AND price IS NOT NULL "
            "AND COALESCE(synthetic, 0)=0 "
            "ORDER BY fetched_at DESC LIMIT 1",
            (origin, dest, dep_date, return_date, passengers),
        ).fetchone()
    if row is None:
        return None
    fetched = datetime.fromisoformat(row["fetched_at"])
    if datetime.now() - fetched > timedelta(hours=CACHE_TTL_HOURS):
        return None
    return _with_offer(row)


def price_history(
    origin: str, dest: str, dep_date: str, return_date: str | None = None,
    passengers: str = "1-0-0", synthetic: bool | None = None,
) -> list[dict]:
    """Все снапшоты маршрута+даты за всё время (для графика динамики).

    synthetic=None — реальные и демо-точки вместе (график);
    synthetic=False — только реальные (оценка динамики в alerts_engine);
    synthetic=True — только демо-история.
    """
    cond = ""
    if synthetic is False:
        cond = " AND COALESCE(synthetic, 0)=0 "
    elif synthetic is True:
        cond = " AND synthetic=1 "
    with _conn() as c:
        rows = c.execute(
            "SELECT price, currency, fetched_at, COALESCE(synthetic, 0) AS synthetic "
            "FROM price_snapshots "
            "WHERE origin=? AND dest=? AND dep_date=? AND return_date IS ? "
            "AND passengers IS ?" + cond +
            " AND price IS NOT NULL ORDER BY fetched_at",
            (origin, dest, dep_date, return_date, passengers),
        ).fetchall()
    return [dict(r) for r in rows]


def get_news_cache(region_key: str) -> dict | None:
    """Кэшированный дайджест новостей региона, если свежий (<= NEWS_TTL_HOURS)."""
    with _conn() as c:
        row = c.execute(
            "SELECT payload, fetched_at FROM news_cache WHERE region_key=?",
            (region_key,),
        ).fetchone()
    if row is None:
        return None
    fetched = datetime.fromisoformat(row["fetched_at"])
    if datetime.now() - fetched > timedelta(hours=NEWS_TTL_HOURS):
        return None
    try:
        return json.loads(row["payload"])
    except json.JSONDecodeError:
        return None


def set_news_cache(region_key: str, payload: dict) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO news_cache (region_key, payload, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(region_key) DO UPDATE SET payload=excluded.payload, "
            "fetched_at=excluded.fetched_at",
            (region_key, json.dumps(payload, ensure_ascii=False), datetime.now().isoformat()),
        )


def _with_offer(row: sqlite3.Row) -> dict:
    """Row -> dict, cheapest_offer JSON -> dict."""
    d = dict(row)
    if d.get("cheapest_offer"):
        try:
            d["cheapest_offer"] = json.loads(d["cheapest_offer"])
        except (json.JSONDecodeError, TypeError):
            d["cheapest_offer"] = None
    return d
