"""CLI-проверка скрейпера Google Flights.

Использует канонический модуль `backend.app.tools.gf`.
Запуск из корня проекта:  .venv/bin/python scripts/scrape_gf.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.tools.gf import search_flights


if __name__ == "__main__":
    res = search_flights("MOW", "TYO", "2027-03-20")
    prices = sorted(f["price"] for f in res)
    print(f"Найдено офферов: {len(res)}")
    if prices:
        print(f"Мин. цена (RUB): {prices[0]:,}")
        print(f"Макс. цена (RUB): {prices[-1]:,}")
        print("Первые 5 офферов:")
        for f in res[:5]:
            seg = f["segments"][0] if f["segments"] else {}
            print(
                f"  {f['price']:,} RUB | airlines={f['airlines']} | "
                f"{seg.get('from')}→{seg.get('to')}"
            )
    else:
        print("Офферы не найдены.")
