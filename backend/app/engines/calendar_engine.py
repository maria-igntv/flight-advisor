"""calendar_engine — занятость календаря VK Workspace.

Web-процесс не умеет ходить в VKWS MCP (он доступен только внутри
Claude Code), поэтому движок читает **снапшот** событий
`data/calendar_busy.json`, выгруженный из календаря VK Workspace
(MCP calendar-list-events; как обновлять — см. HANDOFF.md, Этап 4).

Формат снапшота: {"events": [{"date": "YYYY-MM-DD", "time": "11:00–11:30",
"title": "..."}, ...]} — события уже развёрнуты по датам (включая рекуррентные).
"""
from __future__ import annotations

import json
from pathlib import Path

# backend/app/engines/ → parents[3] = корень проекта (там лежит data/).
_DATA = Path(__file__).resolve().parents[3] / "data" / "calendar_busy.json"


def _load() -> list[dict]:
    if not _DATA.exists():
        return []
    try:
        return json.loads(_DATA.read_text(encoding="utf-8")).get("events", [])
    except (json.JSONDecodeError, OSError):
        return []


def has_data() -> bool:
    """Есть ли снапшот календаря (иначе сигнал занятости не показываем)."""
    return bool(_load())


def busy_dates(from_date, to_date) -> dict[str, list[dict]]:
    """Занятые дни диапазона (включительно).

    Возвращает {"YYYY-MM-DD": [{"time": "11:00–11:30", "title": "..."}, ...]}.
    Сравнение дат — лексикографическое по ISO, для ISO-строк это корректно.
    """
    lo, hi = str(from_date), str(to_date)
    out: dict[str, list[dict]] = {}
    for ev in _load():
        d = ev.get("date")
        if not d or not (lo <= d <= hi):
            continue
        out.setdefault(d, []).append(
            {"time": ev.get("time", ""), "title": ev.get("title", "")}
        )
    return out
