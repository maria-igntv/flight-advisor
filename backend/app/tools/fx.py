"""fx — курсы валют для конвертации цен билетов.

Основной источник — ЦБ РФ (зеркало cbr-xml-daily.ru, JSON без ключа),
fallback — open.er-api.com (тоже без ключа). Оба дают кросс-курсы через
национальную валюту; курс base→quote считается из таблицы «сколько RUB
стоит единица валюты». Результат кэшируется в SQLite (fx_rates), чтобы
не дёргать источник на каждый поиск.
"""
from __future__ import annotations

from .. import db

# «Сколько RBO стоит единица валюты»: источники отдают {"USD": 80.1, "EUR": 92.3, ...}
_CBR_URL = "https://www.cbr-xml-daily.ru/daily_json.js"
_ERAPI_URL = "https://open.er-api.com/v6/latest/USD"


def _rates_from_cbr() -> dict[str, float] | None:
    try:
        import json

        from .net import http_get

        data = json.loads(http_get(_CBR_URL, timeout=20))
        rates = {"RUB": 1.0}
        for code, v in data.get("Valute", {}).items():
            nominal = v.get("Nominal") or 1
            rates[code] = float(v["Value"]) / float(nominal)
        return rates if "USD" in rates else None
    except Exception:  # noqa: BLE001 — источник недоступен: пробуем fallback
        return None


def _rates_from_erapi() -> dict[str, float] | None:
    try:
        import json

        from .net import http_get

        data = json.loads(http_get(_ERAPI_URL, timeout=20))
        rates = data.get("rates") or {}
        return dict(rates) if "USD" in rates and "RUB" in rates else None
    except Exception:  # noqa: BLE001 — оба источника недоступны
        return None


def _cross(rates: dict[str, float], base: str, quote: str) -> float | None:
    """Курс base→quote из таблицы «единица валюты = N единиц номинала».

    Номинал у ЦБ РФ — RUB, у er-api — USD; формула одна: 1 base =
    rates[base] номинала = rates[base]/rates[quote] единиц quote.
    """
    if base not in rates or quote not in rates:
        return None
    return rates[base] / rates[quote]


def get_rate(base: str, quote: str) -> float:
    """Сколько единиц quote стоит единица base (для конвертации цен).

    Кэш в SQLite (TTL FX_TTL_HOURS); при промахе — ЦБ РФ, затем er-api.
    Raises RuntimeError, если курс достать не удалось.
    """
    base, quote = base.upper(), quote.upper()
    if base == quote:
        return 1.0

    cached = db.latest_fx_rate(base, quote)
    if cached is not None:
        return cached

    rates = _rates_from_cbr() or _rates_from_erapi()
    if rates is None:
        raise RuntimeError(
            "Не удалось получить курс валют (ЦБ РФ и er-api недоступны)."
        )
    rate = _cross(rates, base, quote)
    if rate is None or rate <= 0:
        raise RuntimeError(f"Нет курса {base}->{quote} в источниках.")
    db.save_fx_rate(base, quote, rate)
    return rate
