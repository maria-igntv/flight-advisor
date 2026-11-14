"""alerts_engine — динамика цены по накопленной истории снапшотов.

Сравниваем последнюю цену с медианой предыдущих измерений (медиана
устойчивее среднего к выбросам). История копится фоновым поллером
(`scripts/poll_prices.py`, цикл 4 ч) — пока измерений мало, честно
возвращаем статус «collecting».
"""
from __future__ import annotations

import statistics

from .. import db

# Меньше этого процента считаем цену «стабильной».
STABLE_THRESHOLD_PCT = 2.0


def dynamics_from_prices(prices: list[float]) -> dict:
    """Расчёт динамики по уже отобранным прошлым измерениям."""
    if len(prices) < 2:
        return {
            "status": "collecting",
            "measurements": len(prices),
            "direction": None,
            "text": (
                f"Измерений пока {len(prices)} — история копится фоновым поллером "
                "каждые 4 часа. Вывод о динамике цены появится автоматически."
            ),
        }
    latest = prices[-1]
    median = statistics.median(prices[:-1])
    if not median:
        return {
            "status": "collecting",
            "measurements": len(prices),
            "direction": None,
            "text": "Недостаточно корректных измерений для оценки динамики.",
        }
    delta_pct = (latest - median) / median * 100
    if delta_pct < -STABLE_THRESHOLD_PCT:
        direction = "падает"
    elif delta_pct > STABLE_THRESHOLD_PCT:
        direction = "растёт"
    else:
        direction = "стабильно"
    sign = "+" if delta_pct > 0 else ""
    return {
        "status": "ok",
        "latest": latest,
        "median": round(median),
        "delta_pct": round(delta_pct, 1),
        "direction": direction,
        "measurements": len(prices),
        "text": (
            f"Последняя цена {latest:,} ₽ против медианы {round(median):,} ₽ "
            f"по {len(prices) - 1} прошлым измерениям: {direction} ({sign}{delta_pct:.1f}%)."
        ),
    }


def dynamics(
    origin: str,
    dest: str,
    dep_date: str,
    return_date: str | None = None,
    passengers: str = "1-0-0",
) -> dict:
    """Динамика min-цены на конкретную дату вылета по реальным измерениям."""
    db.init_db()
    hist = db.price_history(origin, dest, dep_date, return_date,
                            passengers=passengers, synthetic=False)
    return dynamics_from_prices([h["price"] for h in hist])
