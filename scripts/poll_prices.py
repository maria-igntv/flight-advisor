"""Фоновый поллер цен: накапливает историю снапшотов для отслеживаемых маршрутов.

Запуск (живёт до остановки процесса, лог в data/poller.log):
    cd ~/flight-advisor
    nohup .venv/bin/python scripts/poll_prices.py > data/poller.log 2>&1 &

Каждые POLL_INTERVAL_HOURS часов опрашивает все даты всех маршрутов из
TRACKED_ROUTES (config.py) с паузой POLL_REQUEST_PAUSE_SEC между запросами.
force_refresh=True обязателен — иначе 24ч-кэш пропустит измерения.
"""
from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.config import POLL_INTERVAL_HOURS, POLL_REQUEST_PAUSE_SEC, TRACKED_ROUTES
from backend.app.engines.price_engine import get_price_calendar


def _log(msg: str) -> None:
    print(f"{datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)


def run_cycle() -> tuple[int, int]:
    """Один проход по всем маршрутам. Возвращает (успешных дат, всего дат)."""
    ok = total = 0
    for origin, dest, start_s, end_s in TRACKED_ROUTES:
        start = date.fromisoformat(start_s)
        end = date.fromisoformat(end_s)
        _log(f"{origin}->{dest} {start_s}..{end_s}: запрашиваю")
        d = start
        while d <= end:
            try:
                cal = get_price_calendar(
                    origin, dest, d, d, force_refresh=True
                )
                c = cal[0]
                total += 1
                if c["price"] is not None:
                    ok += 1
                _log(f"  {c['dep_date']}: {c['price']}")
            except Exception as e:  # noqa: BLE001 — поллер не должен умирать
                _log(f"  {d.isoformat()}: ОШИБКА {type(e).__name__}: {e}")
            d += timedelta(days=1)
            time.sleep(POLL_REQUEST_PAUSE_SEC)
    return ok, total


def main() -> None:
    _log(f"поллер запущен: {len(TRACKED_ROUTES)} маршрутов, цикл каждые {POLL_INTERVAL_HOURS} ч")
    while True:
        started = time.monotonic()
        ok, total = run_cycle()
        _log(f"цикл завершён: {ok}/{total} дат с ценами")
        sleep_s = POLL_INTERVAL_HOURS * 3600 - (time.monotonic() - started)
        if sleep_s > 0:
            time.sleep(sleep_s)


if __name__ == "__main__":
    main()
