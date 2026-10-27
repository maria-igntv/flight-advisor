"""Словарь аэропортов для дропдаунов UI.

Данные: backend/app/data/airports.json — курируемый список (~180):
городские агрегаты (MOW, TYO, …) + конкретные аэропорты.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "airports.json"

OTHER = "__other__"  # «Другой (ввести IATA вручную)» в UI


@lru_cache(maxsize=1)
def _raw() -> dict:
    with open(_DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_entries() -> list[dict]:
    """Все пункты для дропдауна: [{code, label}], отсортированные по городу."""
    data = _raw()
    entries: list[dict] = []

    def label(code: str, city: str, name: str, country: str) -> str:
        # «MOW — Москва (все аэропорты)» / «SVO — Москва · Шереметьево»
        if not name:
            return f"{code} — {city}"
        return f"{code} — {city} · {name}"

    for code, city in data["cities"].items():
        entries.append(
            {"code": code, "label": label(code, city, "", ""), "sort": f"0{city}"}
        )
    for code, (city, name, country) in data["airports"].items():
        entries.append(
            {"code": code, "label": label(code, city, name, country), "sort": city}
        )
    entries.sort(key=lambda e: e["sort"])
    for e in entries:
        e.pop("sort")
    return entries


def label_for(code: str) -> str:
    """Людская подпись для IATA-кода (для чата и подписей)."""
    code = code.strip().upper()
    data = _raw()
    if code in data["cities"]:
        return f"{code} ({data['cities'][code]})"
    if code in data["airports"]:
        city, name, country = data["airports"][code]
        return f"{code} ({city}, {country})" if country else f"{code} ({city})"
    return code


def region_for(code: str) -> tuple[str | None, str | None]:
    """(город, страна) для новостного дайджеста региона назначения."""
    code = code.strip().upper()
    data = _raw()
    if code in data["cities"]:
        return data["cities"][code], data.get("city_countries", {}).get(code)
    if code in data["airports"]:
        city, _name, country = data["airports"][code]
        return city, country
    return None, None
