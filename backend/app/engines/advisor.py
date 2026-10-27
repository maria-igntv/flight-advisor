"""advisor — полный отчёт «когда покупать» для одного поиска.

Единая точка для Streamlit UI и FastAPI: цены → регион/виза → новости →
занятость календаря → динамика → вердикт оркестратора.
"""
from __future__ import annotations

from datetime import date

from ..config import CURRENCY
from ..tools.airports import region_for
from . import calendar_engine
from .alerts_engine import dynamics
from .forecast_engine import forecast_curve, forecast_summary
from .news_engine import get_news
from .orchestrator import verdict as make_verdict
from .price_engine import recommend
from .visa_engine import check_trip


def analyze(
    origin: str,
    dest: str,
    dep_start: date,
    dep_end: date | None = None,
    currency: str = CURRENCY,
    return_date: date | None = None,
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    with_news: bool = True,
) -> dict:
    """Результат recommend() + _region, _visa, news, _busy, dynamics, verdict."""
    dep_end_eff = dep_end or dep_start
    res = recommend(
        origin, dest, dep_start, dep_end_eff, currency,
        return_date=return_date, adults=adults, children=children, infants=infants,
    )
    city, country = region_for(dest)
    res["_region"] = {"city": city, "country": country}
    # Окно поездки для визы и календаря: вылет → возвращение (или конец диапазона).
    trip_to = return_date or dep_end_eff
    res["_visa"] = check_trip(country, dep_start, trip_to)

    res["news"] = None
    if with_news and country:
        try:
            res["news"] = get_news(country, city)
        except Exception:  # noqa: BLE001 — новости не должны ломать поиск
            res["news"] = None

    busy_map = None
    if calendar_engine.has_data():
        busy_map = calendar_engine.busy_dates(dep_start.isoformat(), trip_to.isoformat())
        res["_busy"] = busy_map

    if res.get("cheapest_date"):
        res["dynamics"] = dynamics(
            origin, dest, res["cheapest_date"]["dep_date"],
            return_date.isoformat() if return_date else None,
            passengers=f"{adults}-{children}-{infants}",
        )
        res["verdict"] = make_verdict(res, res["news"], res["dynamics"], busy=busy_map)
    return res


def forecast_for(res: dict, dep_iso: str | None = None) -> dict:
    """Прогноз цены от сегодня до вылета для даты dep_iso (по умолчанию — самой дешёвой)."""
    cheapest = res.get("cheapest_date")
    dep_iso = dep_iso or (cheapest or {}).get("dep_date")
    row = next((c for c in res.get("calendar", []) if c["dep_date"] == dep_iso), None)
    if not row or row.get("price") is None:
        return {}
    risk = (res.get("news") or {}).get("risk_level") == "warning"
    fc = forecast_curve(row["price"], date.today(), date.fromisoformat(dep_iso), risk_warning=risk)
    if fc:
        fc["summary"] = forecast_summary(fc, row["price"], res.get("currency", CURRENCY))
    return fc
