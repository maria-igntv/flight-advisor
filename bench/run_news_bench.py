"""Контрольные примеры новостного дайджеста: ожидание и факт сохраняются в JSON/MD."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend.app.engines import news_engine

ROOT = Path(__file__).resolve().parent.parent


def item(title: str, link: str, source: str, age_days: float = 0) -> dict:
    return {
        "title": title,
        "description": title,
        "link": link,
        "published": datetime.now(timezone.utc) - timedelta(days=age_days),
        "source": source,
        "matched_title": True,
    }


def run_case(case: dict) -> dict:
    with patch.object(news_engine, "_collect_guardian", side_effect=lambda cfg: (case["guardian"], case["guardian_error"])), \
         patch.object(news_engine, "_collect_tass", side_effect=lambda cfg, country, city: (case["tass"], case["tass_error"])):
        actual = news_engine.get_news("Япония", "Токио", use_cache=False)
    ok = bool(case["check"](actual))
    return {
        "id": case["id"], "query": case["query"], "expected": case["expected"],
        "ok": ok, "actual": actual,
    }


def main() -> None:
    fresh = item("Тайфун в Японии", "https://example.com/japan/typhoon", "The Guardian")
    old = item("Старая новость о Японии", "https://example.com/japan/old", "The Guardian", age_days=30)
    tass = item("Новости Японии", "https://example.com/japan/tass", "ТАСС")
    cases = [
        {
            "id": "facts_links", "query": "Новости Японии: факты и ссылки",
            "guardian": [fresh], "tass": [], "guardian_error": None, "tass_error": None,
            "expected": "Заголовок и URL совпадают с исходной публикацией",
            "check": lambda r: r["status"] == "ok" and r["digest"][0]["title"] == fresh["title"] and r["digest"][0]["url"] == fresh["link"],
        },
        {
            "id": "period", "query": "Новости Японии: период",
            "guardian": [fresh, old], "tass": [], "guardian_error": None, "tass_error": None,
            "expected": "Старая публикация отфильтрована, свежая осталась",
            "check": lambda r: [d["title"] for d in r["digest"]] == [fresh["title"]],
        },
        {
            "id": "duplicates", "query": "Новости Японии: повторы",
            "guardian": [fresh], "tass": [dict(fresh, source="ТАСС")],
            "guardian_error": None, "tass_error": None,
            "expected": "Одинаковый заголовок показан один раз",
            "check": lambda r: len(r["digest"]) == 1,
        },
        {
            "id": "empty", "query": "Новости Японии: публикаций нет",
            "guardian": [], "tass": [], "guardian_error": None, "tass_error": None,
            "expected": "status=empty и понятное сообщение",
            "check": lambda r: r["status"] == "empty" and not r["digest"] and "не найдено" in r["message"],
        },
        {
            "id": "partial_outage", "query": "Новости Японии: Guardian недоступен, ТАСС работает",
            "guardian": [], "tass": [tass], "guardian_error": "timeout", "tass_error": None,
            "expected": "status=partial, новость ТАСС сохранена, сообщение о сбое",
            "check": lambda r: r["status"] == "partial" and len(r["digest"]) == 1 and r["source_errors"],
        },
        {
            "id": "full_outage", "query": "Новости Японии: оба источника недоступны",
            "guardian": [], "tass": [], "guardian_error": "timeout", "tass_error": "HTTP 503",
            "expected": "status=error, пустой digest, сообщение о сбое",
            "check": lambda r: r["status"] == "error" and not r["digest"] and "недоступны" in r["message"],
        },
    ]
    records = [run_case(case) for case in cases]
    payload = {"cases": len(records), "passed": sum(r["ok"] for r in records), "records": records}
    output = ROOT / "bench" / "results" / "news_bench"
    output.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [
        "# Контрольные примеры новостного дайджеста", "",
        f"Пройдено: **{payload['passed']} из {len(records)}**. Источники имитированы, сетевые вызовы не выполняются; проверяется боевой `get_news()`.", "",
        "| Запрос | Ожидание | Фактический результат | Итог |", "|---|---|---|---|",
    ]
    for record in records:
        actual = record["actual"]
        fact = f"status={actual['status']}; публикаций={len(actual['digest'])}; ошибок источников={len(actual['source_errors'])}"
        lines.append(f"| {record['query']} | {record['expected']} | {fact} | {'PASS' if record['ok'] else 'FAIL'} |")
    lines += ["", "Полные фактические ответы сохранены в `news_bench.json`.", ""]
    output.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"cases": len(records), "passed": payload["passed"]}, ensure_ascii=False))
    if payload["passed"] != len(records):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
