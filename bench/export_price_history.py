"""Экспорт реальных снапшотов цен в воспроизводимый набор для backtest."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "bench" / "data" / "price_history.json")
    args = parser.parse_args()

    con = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT origin, dest, dep_date, return_date, passengers, currency, price, fetched_at
        FROM price_snapshots
        WHERE synthetic = 0 AND price IS NOT NULL
        ORDER BY fetched_at
        """
    ).fetchall()
    con.close()
    payload = {
        "source": "Google Flights min-price snapshots",
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "records": [dict(row) for row in rows],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Экспортировано {len(rows)} снапшотов в {args.output}")


if __name__ == "__main__":
    main()
