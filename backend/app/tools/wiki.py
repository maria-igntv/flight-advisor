"""Справка о городе назначения — краткое досье с Wikipedia (ru).

Используется REST API Wikipedia (api/rest_v1/page/summary): заголовок,
2–3 абзаца-выжимки, ссылка на статью. Если точного заголовка нет
(например, город называется с уточнением) — ищем через opensearch и
берём первое подходящее совпадение. Результат кэшируется на уровне
сессии Streamlit (статьи меняются редко).
"""
from __future__ import annotations

import json
import re
import urllib.parse

from .net import http_get

_SUMMARY_URL = "https://ru.wikipedia.org/api/rest_v1/page/summary/{title}"
_SEARCH_URL = (
    "https://ru.wikipedia.org/w/api.php?action=opensearch&limit=1"
    "&format=json&search={query}"
)


def _clean_city(city_ru: str) -> str:
    """«Денпасар (Бали)» -> «Денпасар»: уточнение в скобках мешает поиску."""
    return re.sub(r"\s*\(.*?\)\s*", " ", city_ru).strip()


def _fetch_summary(title: str) -> dict | None:
    try:
        raw = http_get(_SUMMARY_URL.format(title=urllib.parse.quote(title)))
        data = json.loads(raw)
    except Exception:  # noqa: BLE001 — 404/сеть: пробуем другие способы
        return None
    if not isinstance(data, dict) or not data.get("extract"):
        return None
    return {
        "title": data.get("title", title),
        "extract": data["extract"],
        "url": (
            data.get("content_urls", {})
            .get("desktop", {})
            .get("page", f"https://ru.wikipedia.org/wiki/{urllib.parse.quote(title)}")
        ),
    }


def city_info(city_ru: str | None) -> dict | None:
    """Краткая справка о городе: {title, extract, url} или None.

    Сначала пробуем точный заголовок, потом поиск Wikipedia по названию.
    """
    if not city_ru:
        return None
    title = _clean_city(city_ru)
    if not title:
        return None
    info = _fetch_summary(title)
    if info:
        return info
    # Точного заголовка нет — ищем и пробуем первый результат.
    try:
        raw = http_get(_SEARCH_URL.format(query=urllib.parse.quote(title)))
        found = json.loads(raw)
        if found and len(found) > 1 and found[1]:
            return _fetch_summary(found[1][0])
    except Exception:  # noqa: BLE001 — недоступна Wikipedia: справки не будет
        return None
    return None
