"""Прогноз цены: CatBoost на синтетике, эвристика как fallback.

Модель обучена и проверена только на синтетических данных. Она не является
подтверждённым прогнозом реальных цен: для этого нужна история минимум за
5–6 месяцев. Текущая реальная цена используется как якорь нормировки.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from catboost import CatBoostRegressor

from .demo_history import _dbd_multiplier

UNCERTAINTY_BASE = 0.02
UNCERTAINTY_PER_DAY = 0.0025
_MODEL_FILE = Path(__file__).resolve().parent.parent / "data" / "price_model.cbm"
_META_FILE = _MODEL_FILE.with_suffix(".json")


@lru_cache(maxsize=1)
def _load_model():
    if not _MODEL_FILE.exists() or not _META_FILE.exists():
        return None, {}
    model = CatBoostRegressor()
    model.load_model(_MODEL_FILE)
    metadata = json.loads(_META_FILE.read_text(encoding="utf-8"))
    return model, metadata


def _features(purchase: date, target: date, dep: date) -> list[float]:
    return [
        (target - purchase).days, (dep - target).days, purchase.weekday(), purchase.month,
        12, dep.weekday(), dep.month,
    ]


def _model_prices(anchor_price: float, today: date, dep: date) -> list[float] | None:
    model, metadata = _load_model()
    if model is None:
        return None
    days = []
    current = today
    while current <= dep:
        days.append(current)
        current += timedelta(days=1)
    changes = model.predict([_features(today, day, dep) for day in days])
    if not len(changes):
        return None
    prices = [anchor_price * (1 + (float(value) - float(changes[0])) / 100) for value in changes]
    return prices if all(price > 0 for price in prices) else None


def forecast_curve(
    anchor_price: float,
    today: date,
    dep: date,
    risk_warning: bool = False,
) -> dict:
    """Ежедневная кривая от сегодня до вылета с якорем на реальной цене."""
    if dep <= today or anchor_price <= 0:
        return {}
    model, metadata = _load_model()
    ml_prices = _model_prices(anchor_price, today, dep)
    model_name = metadata.get("version", "catboost") if ml_prices is not None else "heuristic-fallback"
    k_today = _dbd_multiplier((dep - today).days)
    points = []
    current = today
    while current <= dep:
        index = (current - today).days
        horizon = index
        if ml_prices is not None:
            price = ml_prices[index]
        else:
            price = anchor_price * _dbd_multiplier((dep - current).days) / k_today
        unc = UNCERTAINTY_BASE + UNCERTAINTY_PER_DAY * horizon
        low = price * (1 - unc)
        high = price * (1 + unc * (2.0 if risk_warning else 1.0))
        if risk_warning:
            low = price * (1 - unc * 1.25)
        points.append({
            "date": current.isoformat(),
            "price": round(price),
            "low": round(low),
            "high": round(high),
        })
        current += timedelta(days=1)
    pmin = min(points, key=lambda point: point["price"])
    pmax = max(points, key=lambda point: point["price"])
    return {
        "points": points,
        "min": pmin,
        "max": pmax,
        "risk_warning": risk_warning,
        "model": model_name,
        "trained_on": "synthetic" if ml_prices is not None else "none",
    }


def forecast_summary(fc: dict, anchor_price: float, currency: str = "RUB") -> str:
    """Текстовый вывод по кривой прогноза: когда минимум, когда максимум."""
    if not fc:
        return ""
    pmin, pmax = fc["min"], fc["max"]
    growth = (pmax["price"] / anchor_price - 1) * 100 if anchor_price else 0
    d = lambda iso: date.fromisoformat(iso).strftime("%d.%m.%Y")  # noqa: E731
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
