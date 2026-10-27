"""Бенчмарк источника цен: Google Flights через httpx vs curl + кэш SQLite.

    .venv/bin/python -m bench.run_scraper_bench

Результат: bench/results/scraper_bench.json и .md. Для каждого маршрута —
один запрос httpx и один curl (в чередующемся порядке), затем чтение того же
ключа из кэша SQLite через price_engine.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from backend.app.engines.price_engine import get_price_calendar  # noqa: E402
from backend.app.tools import gf  # noqa: E402

ROUTES = [
    ("MOW", "IST", "2026-12-15"), ("MOW", "TYO", "2027-03-20"), ("MOW", "DXB", "2027-01-10"),
    ("LED", "AER", "2027-01-03"), ("MOW", "BKK", "2027-02-05"), ("MOW", "EVN", "2026-11-13"),
    ("MOW", "LON", "2026-11-24"), ("MOW", "BAX", "2026-11-30"), ("KZN", "SVX", "2026-11-14"),
    ("MOW", "PAR", "2027-05-01"),
]


def _query(o, d, day):
    return gf._build_query(o, d, day, None, "RUB", gf._pax(1, 0, 0)).params()


def _via_httpx(params) -> tuple[float, str]:
    t0 = time.perf_counter()
    r = httpx.get(gf.GOOGLE_FLIGHTS_URL, params=params, headers=gf.HEADERS,
                  cookies=gf.COOKIES, timeout=20, follow_redirects=True)
    return time.perf_counter() - t0, r.text


def _via_curl(params) -> tuple[float, str]:
    t0 = time.perf_counter()
    html, _ = gf._curl(f"{gf.GOOGLE_FLIGHTS_URL}?{urlencode(params)}")
    return time.perf_counter() - t0, html


def main() -> None:
    rows = []
    for i, (o, d, day) in enumerate(ROUTES):
        params = _query(o, d, day)
        order = [("httpx", _via_httpx), ("curl", _via_curl)]
        if i % 2:
            order.reverse()
        row = {"route": f"{o}->{d}", "date": day}
        for name, fn in order:
            try:
                dt, html = fn(params)
                fl = gf.parse_flights(html)
                priced = [f["price"] for f in fl if f["price"] is not None]
                row[name] = {"latency": round(dt, 2), "kind": gf.page_kind(html),
                             "offers": len(fl), "priced": len(priced),
                             "min_price": min(priced) if priced else None}
            except Exception as e:  # noqa: BLE001
                row[name] = {"error": f"{type(e).__name__}: {e}"[:200]}
            time.sleep(2)
        get_price_calendar(o, d, date.fromisoformat(day), date.fromisoformat(day))  # прогрев кэша
        t0 = time.perf_counter()
        cal = get_price_calendar(o, d, date.fromisoformat(day), date.fromisoformat(day))
        row["cache"] = {"latency": round(time.perf_counter() - t0, 4), "hit": bool(cal and cal[0]["cached"])}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))

    def agg(name):
        ok = [r[name] for r in rows if "latency" in r.get(name, {})]
        lat = [x["latency"] for x in ok]
        return {
            "requests": len(rows),
            "errors": len(rows) - len(ok),
            "flights_page_pct": round(100 * sum(x["kind"] == "flights" for x in ok) / len(rows), 1),
            "priced_pct": round(100 * sum(x["priced"] > 0 for x in ok) / len(rows), 1),
            "latency_p50": round(statistics.median(lat), 2) if lat else None,
            "latency_max": round(max(lat), 2) if lat else None,
        }

    cache_lat = [r["cache"]["latency"] for r in rows if r["cache"]["hit"]]
    summary = {"httpx": agg("httpx"), "curl": agg("curl"),
               "cache": {"hits": len(cache_lat), "latency_p50_ms": round(1000 * statistics.median(cache_lat), 1) if cache_lat else None}}
    meta = {"started_at": datetime.now().isoformat(timespec="seconds"), "routes": len(ROUTES),
            "where": "российский IP (локальный прогон)"}
    out = ROOT / "bench" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "scraper_bench.json").write_text(
        json.dumps({"meta": meta, "summary": summary, "rows": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    md = (f"Прогон {meta['started_at']}, {meta['routes']} маршрутов, {meta['where']}.\n\n"
          "| Метрика | httpx | curl |\n|---|---|---|\n"
          f"| Страница с выдачей, % | {summary['httpx']['flights_page_pct']} | {summary['curl']['flights_page_pct']} |\n"
          f"| Есть хотя бы одна цена, % | {summary['httpx']['priced_pct']} | {summary['curl']['priced_pct']} |\n"
          f"| Латентность p50 / max, с | {summary['httpx']['latency_p50']} / {summary['httpx']['latency_max']} | "
          f"{summary['curl']['latency_p50']} / {summary['curl']['latency_max']} |\n"
          f"| Ошибки | {summary['httpx']['errors']} | {summary['curl']['errors']} |\n\n"
          f"Чтение из кэша SQLite: p50 = {summary['cache']['latency_p50_ms']} мс "
          f"({summary['cache']['hits']}/{len(rows)} попаданий).\n")
    (out / "scraper_bench.md").write_text(md, encoding="utf-8")
    print("\n" + md)


if __name__ == "__main__":
    main()
