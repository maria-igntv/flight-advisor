"""price_engine — календарь цен + базовая рекомендация (Этап 1).

Полный ML-прогноз (CatBoost) появится позже, когда накопится история.
Здесь — min-цена на каждую дату вылета + эвристика «покупай заранее».
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from .. import db
from ..config import CURRENCY
from ..tools.gf import search_flights


def _pax_key(adults: int, children: int, infants: int) -> str:
    """Ключ изоляции кэша/истории по составу пассажиров («взр-дет-мл»)."""
    return f"{adults}-{children}-{infants}"


def get_price_calendar(
    origin: str,
    dest: str,
    start_date: date,
    end_date: date,
    currency: str = CURRENCY,
    force_refresh: bool = False,
    return_date: date | None = None,
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
) -> list[dict]:
    """Min-цена на каждую дату вылета в [start_date, end_date].

    При return_date — цены туда-обратно (итог за оба плеча; Google Flights
    на первой странице отдаёт итоговую цену + детали плеча «туда»).
    Цена всегда указана за ВСЕХ пассажиров (взрослые/дети/младенцы).

    Кэш: свежий снапшот (<= CACHE_TTL_HOURS) берём из SQLite, иначе
    запрашиваем Google Flights и сохраняем. One-way (return_date IS NULL),
    round-trip и разный состав пассажиров не смешиваются.
    """
    db.init_db()
    ret_iso = return_date.isoformat() if return_date else None
    pax = _pax_key(adults, children, infants)
    results: list[dict] = []
    d = start_date
    while d <= end_date:
        iso = d.isoformat()
        cached = db.latest_snapshot(origin, dest, iso, ret_iso, passengers=pax)
        if cached and not force_refresh:
            results.append(
                {
                    "dep_date": iso,
                    "price": cached["price"],
                    "offers": None,
                    "airlines": json.loads(cached["airlines"]) if cached["airlines"] else [],
                    "cheapest_offer": cached.get("cheapest_offer"),
                    "fetched_at": cached["fetched_at"],
                    "cached": True,
                }
            )
        else:
            flights = search_flights(
                origin, dest, iso, return_date=ret_iso, currency=currency,
                adults=adults, children=children, infants=infants,
            )
            # Часть офферов может прийти без цены (перевозчик не отдаёт цены
            # в Google Flights — проверено на MOW->BAX): такие не участвуют
            # в min(), но считаются отдельно для честного сообщения в UI.
            priced = [f for f in flights if f["price"] is not None]
            cheapest = min(priced, key=lambda f: f["price"]) if priced else None
            price = cheapest["price"] if cheapest else None
            airlines = cheapest["airlines"] if cheapest else (
                flights[0]["airlines"] if flights else []
            )
            db.save_snapshot(
                origin, dest, iso, price, airlines, currency,
                return_date=ret_iso, cheapest_offer=cheapest, passengers=pax,
            )
            results.append(
                {
                    "dep_date": iso,
                    "price": price,
                    "offers": len(flights),
                    "unpriced_offers": len(flights) - len(priced),
                    "airlines": airlines,
                    "cheapest_offer": cheapest,
                    "fetched_at": None,
                    "cached": False,
                }
            )
        d += timedelta(days=1)
    return results


def recommend(
    origin: str,
    dest: str,
    start_date: date,
    end_date: date,
    currency: str = CURRENCY,
    return_date: date | None = None,
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
) -> dict:
    """Календарь цен + самая дешёвая дата + эвристическая рекомендация."""
    calendar = get_price_calendar(
        origin, dest, start_date, end_date, currency, return_date=return_date,
        adults=adults, children=children, infants=infants,
    )
    priced = [c for c in calendar if c["price"] is not None]
    cheapest = min(priced, key=lambda c: c["price"]) if priced else None

    return {
        "origin": origin,
        "dest": dest,
        "currency": currency,
        "return_date": return_date.isoformat() if return_date else None,
        "passengers": {"adults": adults, "children": children, "infants": infants},
        "calendar": calendar,
        "cheapest_date": cheapest,
        "recommendation": _heuristic(cheapest, len(calendar), len(priced), bool(return_date)),
    }


def _heuristic(
    cheapest: dict | None, total: int, priced: int, round_trip: bool = False
) -> dict:
    if cheapest is None:
        return {
            "signal": "no_data",
            "text": "Цены не получены (нет рейсов или источник недоступен).",
            "confidence": "low",
        }
    trip = "туда-обратно" if round_trip else "в одну сторону"
    return {
        "signal": "buy_earlier",
        "text": (
            f"Самая низкая цена {trip} сейчас — на {cheapest['dep_date']}: "
            f"{cheapest['price']:,} ₽. Истории ещё мало: полный прогноз «когда "
            "покупать» появится по мере накопления данных. Обычно цены растут "
            "ближе к вылету — в среднем выгоднее покупать заранее."
        ),
        "confidence": "low",
        "note": "Эвристика (нет накопленной истории). CatBoost-прогноз — на следующих этапах.",
    }
