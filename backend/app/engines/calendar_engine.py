"""Занятость VK WorkSpace через MCP; JSON-снапшот — резервный источник."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from ..tools import calendar_mcp

_DATA = Path(__file__).resolve().parents[3] / "data" / "calendar_busy.json"
_CACHE_TTL_SECONDS = 300
_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _load_snapshot() -> list[dict]:
    if not _DATA.exists():
        return []
    try:
        return json.loads(_DATA.read_text(encoding="utf-8")).get("events", [])
    except (json.JSONDecodeError, OSError):
        return []


def _events_to_busy(events: list[dict], from_date: str, to_date: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for event in events:
        day = event.get("date")
        if not day or not (from_date <= day <= to_date):
            continue
        out.setdefault(day, []).append(
            {"time": event.get("time", ""), "title": event.get("title", "занято")}
        )
    return out


def _mcp_events(from_date: str, to_date: str) -> dict:
    key = (from_date, to_date)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]
    payload = calendar_mcp.list_events(from_date, to_date)
    with _cache_lock:
        _cache[key] = (now, payload)
    return payload


def calendar_data(from_date, to_date) -> dict:
    """События диапазона и метаданные источника, без падения при недоступном MCP."""
    lo, hi = str(from_date), str(to_date)
    if calendar_mcp.configured():
        try:
            payload = _mcp_events(lo, hi)
            return {
                "has_data": True,
                "busy": _events_to_busy(payload["events"], lo, hi),
                "source": "mcp",
                "fetched_at": payload.get("fetched_at"),
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001 — резервный снапшот не должен ломать поиск
            if _DATA.exists():
                return {
                    "has_data": True,
                    "busy": _events_to_busy(_load_snapshot(), lo, hi),
                    "source": "snapshot_fallback",
                    "fetched_at": None,
                    "error": f"MCP недоступен ({type(exc).__name__})",
                }
            return {
                "has_data": False,
                "busy": {},
                "source": "mcp",
                "fetched_at": None,
                "error": f"MCP недоступен ({type(exc).__name__})",
            }
    if _DATA.exists():
        return {
            "has_data": True,
            "busy": _events_to_busy(_load_snapshot(), lo, hi),
            "source": "snapshot",
            "fetched_at": None,
            "error": None,
        }
    return {"has_data": False, "busy": {}, "source": "none", "fetched_at": None, "error": None}


def has_data() -> bool:
    return calendar_mcp.configured() or _DATA.exists()


def busy_dates(from_date, to_date) -> dict[str, list[dict]]:
    """Занятые дни диапазона (включительно)."""
    return calendar_data(from_date, to_date)["busy"]
