"""visa_engine — правила въезда для граждан РФ в страну назначения.

Справочник `data/visa_rules.json` (статический, по всем странам из
airports.json). Сверяет длительность поездки (вылет → возвращение или
конец диапазона вылетов) с безвизовым лимитом и формирует предупреждение
для UI: нужна ли виза / электронное разрешение и укладывается ли поездка
в разрешённый срок.

Данные справочные — в UI обязательно подчёркиваем, что перед поездкой
правила надо перепроверить (МИД/консульство).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

_RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "visa_rules.json"
_rules_cache: dict | None = None

# Человекочитаемые названия режимов для UI.
REGIME_TITLES = {
    "visa_free": "Безвизовый въезд",
    "e_visa": "Электронная виза",
    "visa_on_arrival": "Виза по прилёту",
    "visa": "Нужна виза",
}


def _rules() -> dict:
    global _rules_cache
    if _rules_cache is None:
        _rules_cache = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    return _rules_cache


def entry_rules(country_ru: str | None) -> dict | None:
    """Правила въезда для страны: {regime, days, note} или None."""
    if not country_ru:
        return None
    return _rules().get(country_ru)


def check_trip(country_ru: str | None, dep: str | date, ret: str | date | None) -> dict | None:
    """Предупреждение о правилах въезда под конкретную поездку.

    dep — дата вылета, ret — возвращение (или None: одна сторона / диапазон).
    Возвращает None, если страны нет в справочнике или это внутренний
    перелёт по России (предупреждать не о чем). Иначе:
      {regime, regime_title, days, note, trip_days, overstay, headline, text}
    """
    if not country_ru or country_ru == "Россия":
        return None
    rule = entry_rules(country_ru)
    if not rule:
        return None

    dep_d = date.fromisoformat(str(dep)[:10]) if isinstance(dep, str) else dep
    ret_d = (date.fromisoformat(str(ret)[:10]) if isinstance(ret, str) else ret) if ret else None
    trip_days = (ret_d - dep_d).days + 1 if ret_d else None

    regime = rule["regime"]
    days = rule.get("days")
    # Внутренний рейс — визу требует страна назначения, не вылета,
    # поэтому предупреждение показываем для любой зарубежной страны.

    if regime == "visa_free" and days is None:
        # Срок не ограничен — информировать не о чем.
        return None

    overstay = bool(trip_days and days and trip_days > days)
    parts = [REGIME_TITLES.get(regime, regime)]
    if days:
        parts.append(f"до {days} дн.")
    headline = f"🛂 {country_ru}: " + " — ".join(parts)

    text = rule.get("note", "")
    if trip_days and days:
        if overstay:
            text = (
                f"Ваша поездка длится {trip_days} дн., а безвизовый лимит — "
                f"{days} дн.: нужно оформить визу (или сократить поездку). " + text
            )
        else:
            text = f"Ваша поездка ({trip_days} дн.) укладывается в лимит. " + text
    return {
        "regime": regime,
        "regime_title": REGIME_TITLES.get(regime, regime),
        "days": days,
        "note": text,
        "trip_days": trip_days,
        "overstay": overstay,
        "headline": headline,
    }
