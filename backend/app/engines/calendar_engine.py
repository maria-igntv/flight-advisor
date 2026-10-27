"""calendar_engine — занятость календаря из снапшота VK Workspace.

Источник один: статический снапшот `data/calendar_busy.json`, экспортированный
из календаря VK Workspace через MCP (calendar-list-events) и закоммиченный в
репозиторий — поэтому работает и локально, и на Streamlit Cloud.

Обновление снапшота: повторный экспорт через MCP + git push (см. HANDOFF.md).

Публичный интерфейс (для frontend):
    has_data() -> bool
    busy_dates(from_date, to_date) -> {"YYYY-MM-DD": [{"time", "title"}, ...]}
"""
from __future__ import annotations

import json
from pathlib import Path

# backend/app/engines/ → parents[3] = корень проекта (там лежит data/).
_DATA = Path(__file__).resolve().parents[3] / "data" / "calendar_busy.json"


def _load_snapshot() -> list[dict]:
    if not _DATA.exists():
        return []
    try:
        return json.loads(_DATA.read_text(encoding="utf-8")).get("events", [])
    except (json.JSONDecodeError, OSError):
        return []


def has_data() -> bool:
    return _DATA.exists()


def busy_dates(from_date, to_date) -> dict[str, list[dict]]:
    """Занятые дни диапазона (включительно).

    Возвращает {"YYYY-MM-DD": [{"time": "11:00–11:30", "title": "..."}, ...]}.
    Сравнение дат — лексикографическое по ISO, для ISO-строк это корректно.
    """
    lo, hi = str(from_date), str(to_date)
    out: dict[str, list[dict]] = {}
    for ev in _load_snapshot():
        d = ev.get("date")
        if not d or not (lo <= d <= hi):
            continue
        out.setdefault(d, []).append(
            {"time": ev.get("time", ""), "title": ev.get("title", "занято")}
        )
    return out
