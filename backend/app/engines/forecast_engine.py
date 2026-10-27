"""forecast_engine — эвристический прогноз цены от «сегодня» до даты вылета.

Отвечает на вопрос пользователя «когда покупать»: строит кривую ожидаемой
минимальной цены на выбранную дату вылета от сегодняшнего дня до вылета и
находит на ней точку минимума (рекомендуемое время покупки) и максимума.

Пока накопленной истории мало для CatBoost, прогноз строится по типовой
кривой бронирования (см. demo_history._dbd_multiplier: плато в «оптимальном
окне» ~3–8 месяцев до вылета → плавный рост с ~30 дней → крутой рост в
последние 2 недели). Кривая привязывается («якорь») к текущей реальной цене
из календаря Google Flights и нормируется на сегодня — то есть прогноз
работает для ЛЮБОГО маршрута, не только демо.

Неопределённость растёт с горизонтом. Если news_engine вернул тревожные
события в регионе (risk=warning), верхняя граница расширяется сильнее
нижней — события (катаклизмы, беспорядки) обычно уводят цены вверх.

Прогноз — ДЕМО/эвристика и честно помечается в UI; в БД не пишется
(рассчитывается на лету при открытии графика).
"""
from __future__ import annotations

from datetime import date, timedelta

from .demo_history import _dbd_multiplier

# Неопределённость прогноза: базовая ±2% + 0.25 п.п. за каждый день горизонта
# (на 30 дней вперёд — примерно ±10%).
UNCERTAINTY_BASE = 0.02
UNCERTAINTY_PER_DAY = 0.0025


def forecast_curve(
    anchor_price: float,
    today: date,
    dep: date,
    risk_warning: bool = False,
) -> dict:
    """Кривая прогноза минимальной цены от сегодня до даты вылета.

    anchor_price — текущая реальная цена на эту дату вылета (за всех
    пассажиров, в валюте поиска). today — «сегодня», dep — дата вылета.

    Возвращает:
      {
        "points": [{date, price, low, high}, ...]   — ежедневные точки;
        "min":  {date, price}                        — ожидаемый минимум;
        "max":  {date, price}                        — ожидаемый максимум;
        "risk_warning": bool,
      }
    Пустой словарь, если вылет уже сегодня/в прошлом или якорь некорректен.
    """
    if dep <= today or anchor_price <= 0:
        return {}
    # Нормируем кривую так, чтобы в «сегодня» прогноз совпадал с фактом.
    k_today = _dbd_multiplier((dep - today).days)
    points = []
    d = today
    while d <= dep:
        horizon = (d - today).days
        price = anchor_price * _dbd_multiplier((dep - d).days) / k_today
        unc = UNCERTAINTY_BASE + UNCERTAINTY_PER_DAY * horizon
        low = price * (1 - unc)
        # При тревожных новостях: полоса шире в обе стороны и асимметрична
        # вверх — «хвост риска» на повышение.
        high = price * (1 + unc * (2.0 if risk_warning else 1.0))
        if risk_warning:
            low = price * (1 - unc * 1.25)
        points.append({
            "date": d.isoformat(),
            "price": round(price),
            "low": round(low),
            "high": round(high),
        })
        d += timedelta(days=1)
    # min() при плато возвращает первую точку минимума (самая ранняя дата
    # покупки по минимальной цене) — это и есть рекомендуемое «когда покупать».
    pmin = min(points, key=lambda p: p["price"])
    pmax = max(points, key=lambda p: p["price"])
    return {"points": points, "min": pmin, "max": pmax, "risk_warning": risk_warning}


def forecast_summary(fc: dict, anchor_price: float, currency: str = "RUB") -> str:
    """Текстовый вывод по кривой прогноза: когда минимум, когда максимум."""
    if not fc:
        return ""
    pmin, pmax = fc["min"], fc["max"]
    growth = (pmax["price"] / anchor_price - 1) * 100 if anchor_price else 0
    d = lambda iso: date.fromisoformat(iso).strftime("%d.%m.%Y")  # noqa: E731
    # Минимум обычно не одна дата, а «окно» (плато оптимального бронирования):
    # покажем диапазон дат с ценой в пределах +0.5% от минимума.
    plateau = [p["date"] for p in fc["points"]
               if p["price"] <= pmin["price"] * 1.005]
    if len(plateau) > 3:
        min_txt = (f"в окне {d(plateau[0])} – {d(plateau[-1])} "
                   "(рекомендуемое время покупки)")
    elif plateau[0] == fc["points"][0]["date"]:
        min_txt = f"с {d(pmin['date'])} (покупать сейчас)"
    else:
        min_txt = f"{d(pmin['date'])} (рекомендуемое время покупки)"
    parts = [
        f"🔻 Минимальная цена ≈ {pmin['price']:,} {currency} ожидается {min_txt}",
        f"🔺 Максимальная ≈ {pmax['price']:,} {currency} к {d(pmax['date'])} "
        f"(+{growth:.0f}% к текущей цене)." if growth > 0 else
        f"🔺 Максимальная ≈ {pmax['price']:,} {currency} ({d(pmax['date'])}).",
    ]
    if fc.get("risk_warning"):
        parts.append(
            "⚠️ В регионе назначения тревожные события — цена может уйти выше "
            "верхней границы полосы."
        )
    return " · ".join(parts)
