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


def dynamics(
    origin: str,
    dest: str,
    dep_date: str,
    return_date: str | None = None,
    passengers: str = "1-0-0",
) -> dict:
    """Динамика min-цены на конкретную дату вылета.

    passengers — ключ состава («взр-дет-мл»), чтобы динамика считалась
    по истории того же состава пассажиров, что и текущий поиск.

    Возвращает:
      {"status": "collecting", measurements, text}          — истории ещё мало;
      {"status": "ok", latest, median, delta_pct, direction, text, measurements}
        direction: "падает" | "стабильно" | "растёт" (последняя цена vs медиана).
    """
    db.init_db()
    # Только РЕАЛЬНЫЕ измерения: демо-история (synthetic=1) — для графика,
    # а вывод «растёт/падает» должен опираться на живые данные поллера.
    hist = db.price_history(origin, dest, dep_date, return_date,
                            passengers=passengers, synthetic=False)
    if len(hist) < 2:
        return {
            "status": "collecting",
            "measurements": len(hist),
            "direction": None,
            "text": (
                f"Измерений пока {len(hist)} — история копится фоновым поллером "
                "каждые 4 часа. Вывод о динамике цены появится автоматически."
            ),
        }

    prices = [h["price"] for h in hist]
    latest = prices[-1]
    median = statistics.median(prices[:-1])
    if not median:
        return {
            "status": "collecting",
            "measurements": len(hist),
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
        "measurements": len(hist),
        "text": (
            f"Последняя цена {latest:,} ₽ против медианы {round(median):,} ₽ "
            f"по {len(hist) - 1} прошлым измерениям: {direction} ({sign}{delta_pct:.1f}%)."
        ),
    }
