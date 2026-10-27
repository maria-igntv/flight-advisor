"""demo_history — синтетическая история цен за прошлый год (демо-режим).

Google Flights не отдаёт исторические цены, а поллер начал копить реальные
измерения только 24.10.2026 — годового графика «цена от сегодня до вылета»
на реальных данных до защиты не будет. Этот модуль генерирует правдоподобную
историю за 365 дней до вылета для демо-маршрутов (топ-3 направления):

- сезонность: пики НГ / сакура (Токио) / лето (Сочи, Стамбул) / майские;
- рост цены по мере приближения даты вылета (типичная кривая бронирования);
- детерминированный шум (crc32 от ключа снапшота) — график стабилен
  между перезапусками приложения;
- все синтетические снапшоты помечаются synthetic=1 и НЕ используются
  как кэш календаря цен (latest_snapshot их игнорирует) и НЕ участвуют
  в оценке динамики (alerts_engine.dynamics смотрит только реальные).

Генерация идемпотентна: вызывается под конкретный ключ поиска при открытии
графика, при следующих запусках дополняет недостающие даты до «сегодня».
"""
from __future__ import annotations

import zlib
from datetime import date, timedelta

from .. import db

# Окно истории: год до вылета. Точки — еженедельно, последние 2 недели — ежедневно.
HISTORY_DAYS = 365
DAILY_TAIL_DAYS = 14

# Демо-маршруты: базовая цена one-way за 1 взрослого (RUB), авиакомпании и
# сезонные пики ((мес, день) начала, (мес, день) конца, множитель).
DEMO_ROUTES: dict[tuple[str, str], dict] = {
    ("MOW", "TYO"): {
        "base": 46_000,
        "airlines": ["Аэрофлот"],
        "peaks": [
            ((3, 20), (4, 10), 1.55),   # сезон сакуры
            ((12, 15), (1, 12), 1.35),  # Новый год (через границу года)
            ((4, 25), (5, 10), 1.30),   # «золотая неделя»
            ((7, 1), (8, 31), 1.15),    # лето
        ],
    },
    ("MOW", "AER"): {
        "base": 9_000,
        "airlines": ["S7 Airlines"],
        "peaks": [
            ((6, 1), (8, 31), 1.60),    # летний пик на море
            ((12, 15), (1, 12), 1.50),  # Новый год
            ((4, 28), (5, 12), 1.35),   # майские
            ((2, 1), (2, 28), 1.20),    # горнолыжный сезон
        ],
    },
    ("MOW", "IST"): {
        "base": 25_000,
        "airlines": ["Turkish Airlines", "Аэрофлот"],
        "peaks": [
            ((6, 15), (9, 1), 1.30),    # лето
            ((12, 15), (1, 12), 1.30),  # Новый год
            ((4, 28), (5, 12), 1.25),   # майские
        ],
    },
}


def _in_peak(d: date, start: tuple[int, int], end: tuple[int, int]) -> bool:
    s = date(d.year, *start)
    e = date(d.year, *end)
    if s <= e:
        return s <= d <= e
    # Диапазон через границу года (например, Новый год).
    return d >= s or d <= e


def _seasonality(cfg: dict, dep: date) -> float:
    mult = 1.0
    for start, end, m in cfg["peaks"]:
        if _in_peak(dep, start, end):
            mult = max(mult, m)
    return mult


def _dbd_multiplier(days_before: int) -> float:
    """Типичная кривая «цена от близости к вылету»: оптимальное окно
    покупки ~3–8 месяцев до вылета, дальше плавный рост, у вылета — крутой."""
    if days_before > 240:
        return 1.0
    if days_before >= 90:
        return 0.92
    if days_before >= 30:
        return 0.92 + 0.20 * (90 - days_before) / 60
    if days_before >= 14:
        return 1.12 + 0.25 * (30 - days_before) / 16
    return 1.37 + 0.35 * (14 - days_before) / 13


def _noise(*parts: object) -> float:
    """Детерминированный «шум рынка» ±4%: crc32 от ключа снапшота."""
    seed = "|".join(str(p) for p in parts).encode()
    return 1 + (zlib.crc32(seed) % 81 - 40) / 1000


def _synthetic_price(
    cfg: dict, dep: date, snap_date: date,
    pax: tuple[int, int, int], return_date: date | None,
) -> int:
    price = cfg["base"] * _seasonality(cfg, dep)
    if return_date is not None:
        # Туда-обратно дороже one-way; чуть дороже длинные поездки.
        trip_days = (return_date - dep).days
        price *= 1.80 + min(trip_days, 21) * 0.004
    adults, children, infants = pax
    price *= adults + 0.75 * children + 0.10 * infants
    price *= _dbd_multiplier((dep - snap_date).days)
    price *= _noise(
        cfg["base"], dep.isoformat(), snap_date.isoformat(),
        return_date.isoformat() if return_date else "", "-".join(map(str, pax)),
    )
    # Округление до сотен — как настоящие тарифы.
    return max(1000, round(price / 100) * 100)


def _snapshot_dates(dep: date, today: date) -> list[date]:
    """Даты снапшотов «как будто собирали»: от вылет-365 до сегодня."""
    first = dep - timedelta(days=HISTORY_DAYS)
    if first >= today:
        return []  # вылет дальше года — вся «история» ещё в будущем
    dates: list[date] = []
    d = first
    while d <= today:
        if (today - d).days <= DAILY_TAIL_DAYS or (d - first).days % 7 == 0:
            dates.append(d)
        d += timedelta(days=1)
    return dates


def ensure_demo_history(
    origin: str,
    dest: str,
    dep_date: str,
    return_date: str | None = None,
    passengers: str = "1-0-0",
) -> bool:
    """Гарантирует наличие демо-истории под ключ поиска.

    Возвращает True, если маршрут — демо и синтетическая история есть/создана.
    Идемпотентно: существующие даты не пересоздаются, недостающие (до
    «сегодня») дополняются при каждом вызове — цены детерминированы.
    """
    origin, dest = origin.upper(), dest.upper()
    cfg = DEMO_ROUTES.get((origin, dest))
    if cfg is None:
        return False
    db.init_db()
    dep = date.fromisoformat(dep_date)
    ret = date.fromisoformat(return_date) if return_date else None
    wanted = _snapshot_dates(dep, date.today())
    if not wanted:
        return False
    have = db.synthetic_dates(origin, dest, dep_date, return_date, passengers)
    pax = tuple(int(x) for x in passengers.split("-"))
    # Калибровка по реальным измерениям: если поллер/поиск уже видел цену
    # этого ключа — масштабируем синтетику так, чтобы кривая стыковалась
    # с фактом (иначе на графике виден ложный «скачок» на стыке).
    scale = 1.0
    real = db.price_history(origin, dest, dep_date, return_date,
                            passengers=passengers, synthetic=False)
    if real:
        last = real[-1]
        anchor = _synthetic_price(cfg, dep, date.fromisoformat(last["fetched_at"][:10]),
                                  pax, ret)
        if anchor > 0:
            # Ограничиваем: калибровка выравнивает уровень, а не «рисует» факт.
            scale = min(2.0, max(0.5, last["price"] / anchor))
    for d in wanted:
        if d.isoformat() in have:
            continue
        db.save_demo_snapshot(
            origin, dest, dep_date,
            round(_synthetic_price(cfg, dep, d, pax, ret) * scale),
            cfg["airlines"], "RUB",
            return_date=return_date, passengers=passengers,
            fetched_at=f"{d.isoformat()}T12:00:00",
        )
    return True
