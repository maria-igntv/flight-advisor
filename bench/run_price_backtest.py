"""Ретроспективная проверка прогноза и сигналов BUY/WAIT на реальных снапшотах.

Запуск: .venv/bin/python -m bench.run_price_backtest
Вход: bench/data/price_history.json из bench.export_price_history.
Выход: bench/results/price_backtest.{json,md}.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

from backend.app.engines.alerts_engine import STABLE_THRESHOLD_PCT
from backend.app.engines.forecast_engine import forecast_curve

ROOT = Path(__file__).resolve().parent.parent
KEY_FIELDS = ("origin", "dest", "dep_date", "return_date", "passengers", "currency")


def group_history(records: list[dict]) -> dict[tuple, list[dict]]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for record in records:
        if record.get("price") and record.get("price") > 0:
            groups[tuple(record.get(k) for k in KEY_FIELDS)].append(record)
    for rows in groups.values():
        rows.sort(key=lambda row: row["fetched_at"])
    return groups


def evaluate_forecasts(groups: dict[tuple, list[dict]]) -> list[dict]:
    out = []
    for rows in groups.values():
        for anchor_index, anchor in enumerate(rows):
            anchor_day = date.fromisoformat(anchor["fetched_at"][:10])
            dep = date.fromisoformat(anchor["dep_date"])
            curve = forecast_curve(anchor["price"], anchor_day, dep)
            if not curve:
                continue
            points = {point["date"]: point for point in curve["points"]}
            for target in rows[anchor_index + 1:]:
                target_day = date.fromisoformat(target["fetched_at"][:10])
                if target_day <= anchor_day:
                    continue
                point = points.get(target_day.isoformat())
                if not point:
                    continue
                record = {
                    **{k: anchor[k] for k in KEY_FIELDS},
                    "anchor_at": anchor["fetched_at"],
                    "target_at": target["fetched_at"],
                    "horizon_days": (target_day - anchor_day).days,
                    "anchor_price": anchor["price"],
                    "predicted_price": point["price"],
                    "low": point["low"],
                    "high": point["high"],
                    "actual_price": target["price"],
                }
                record["abs_pct_error"] = abs(point["price"] - target["price"]) / target["price"] * 100
                record["baseline_abs_pct_error"] = abs(anchor["price"] - target["price"]) / target["price"] * 100
                record["covered"] = point["low"] <= target["price"] <= point["high"]
                out.append(record)
    return out


def evaluate_decisions(groups: dict[tuple, list[dict]]) -> list[dict]:
    out = []
    for rows in groups.values():
        for index in range(len(rows) - 1):
            current = rows[index]
            future = rows[index + 1:]
            today = date.fromisoformat(current["fetched_at"][:10])
            curve = forecast_curve(current["price"], today, date.fromisoformat(current["dep_date"]))
            if not curve:
                continue
            future_points = curve["points"][1:]
            if not future_points:
                continue
            predicted_min = min(future_points, key=lambda point: point["price"])
            current_price = current["price"]
            action = "WAIT" if predicted_min["price"] < current_price else "BUY"
            cheapest = min(future, key=lambda row: row["price"])
            future_min = cheapest["price"]
            change_pct = (future_min - current_price) / current_price * 100
            success = change_pct >= -STABLE_THRESHOLD_PCT if action == "BUY" else change_pct <= -STABLE_THRESHOLD_PCT
            out.append({
                **{k: current[k] for k in KEY_FIELDS},
                "decision_at": current["fetched_at"],
                "future_min_at": cheapest["fetched_at"],
                "action": action,
                "predicted_min_price": predicted_min["price"],
                "predicted_min_at": predicted_min["date"],
                "current_price": current_price,
                "future_min_price": future_min,
                "change_pct": change_pct,
                "success": success,
                "benefit_pct": change_pct if action == "BUY" else -change_pct,
                "benefit_amount": future_min - current_price if action == "BUY" else current_price - future_min,
            })
    return out


def mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def forecast_summary(records: list[dict]) -> dict:
    by_horizon = {}
    for horizon in sorted({r["horizon_days"] for r in records}):
        subset = [r for r in records if r["horizon_days"] == horizon]
        by_horizon[str(horizon)] = {
            "n": len(subset),
            "mape_pct": mean([r["abs_pct_error"] for r in subset]),
            "baseline_mape_pct": mean([r["baseline_abs_pct_error"] for r in subset]),
            "coverage_pct": mean([float(r["covered"]) for r in subset]) * 100,
        }
    return {
        "n": len(records),
        "mape_pct": mean([r["abs_pct_error"] for r in records]),
        "baseline_mape_pct": mean([r["baseline_abs_pct_error"] for r in records]),
        "mae_rub": mean([abs(r["predicted_price"] - r["actual_price"])
                         for r in records if r["currency"] == "RUB"]),
        "baseline_mae_rub": mean([abs(r["anchor_price"] - r["actual_price"])
                                  for r in records if r["currency"] == "RUB"]),
        "coverage_pct": mean([float(r["covered"]) for r in records]) * 100 if records else None,
        "by_horizon": by_horizon,
    }


def decision_summary(records: list[dict]) -> dict:
    by_action = {}
    for action in ("BUY", "WAIT"):
        subset = [r for r in records if r["action"] == action]
        by_action[action] = {
            "n": len(subset),
            "successes": sum(r["success"] for r in subset),
            "accuracy_pct": mean([float(r["success"]) for r in subset]) * 100 if subset else None,
            "mean_benefit_pct": mean([r["benefit_pct"] for r in subset]),
        }
    return {
        "n": len(records),
        "accuracy_pct": mean([float(r["success"]) for r in records]) * 100 if records else None,
        "mean_benefit_pct": mean([r["benefit_pct"] for r in records]),
        "by_action": by_action,
    }


def case_row(record: dict) -> str:
    return (
        f"| {record['action']} | {record['origin']}→{record['dest']} | {record['dep_date']} | "
        f"{record['decision_at'][:16].replace('T', ' ')} | {record['current_price']:.0f} | "
        f"{record['future_min_price']:.0f} | {record['change_pct']:+.1f}% | "
        f"{'успех' if record['success'] else 'ошибка'} |"
    )


def to_markdown(data: dict, forecast: dict, decisions: dict, forecast_records: list[dict], decision_records: list[dict]) -> str:
    lines = [
        "# Проверка прогноза и рекомендаций BUY / WAIT",
        "",
        f"Данные: **{len(data['records'])} реальных снапшотов** Google Flights, "
        f"{data['records'][0]['fetched_at'][:10]} — {data['records'][-1]['fetched_at'][:10]}. "
        "Синтетическая история исключена.",
        "",
        "## Методика",
        "",
        "- Каждый прогноз использует только цену, известную на момент начала прогноза; будущие измерения используются только как факт.",
        "- Baseline — персистентность: прогноз равен текущей цене. MAE/MAPE меньше = лучше; coverage — доля фактов внутри прогнозного коридора.",
        f"- BUY успешен, если будущий минимум не ниже текущей цены более чем на {STABLE_THRESHOLD_PCT:.0f}%. "
        f"WAIT успешен, если позже появляется цена минимум на {STABLE_THRESHOLD_PCT:.0f}% ниже.",
        "- BUY/WAIT ставится по минимуму прогноза от дня решения до вылета. Полный вердикт с календарным spread, новостями и встречами не проверяется: эти сигналы в истории не сохранены.",
        "",
        "## Прогноз цены",
        "",
        "| Метрика | CatBoost (обучен на синтетике) | Baseline |",
        "|---|---:|---:|",
        f"| Пар прогноз→факт | {forecast['n']} | {forecast['n']} |",
        f"| MAPE, % | {forecast['mape_pct']:.2f} | {forecast['baseline_mape_pct']:.2f} |",
        f"| MAE, ₽ (только RUB) | {forecast['mae_rub']:.0f} | {forecast['baseline_mae_rub']:.0f} |",
        f"| Coverage коридора, % | {forecast['coverage_pct']:.1f} | — |",
        "",
        "| Горизонт, дней | Пар | MAPE, % | Baseline MAPE, % | Coverage, % |",
        "|---:|---:|---:|---:|---:|",
    ]
    for horizon, metrics in forecast["by_horizon"].items():
        lines.append(
            f"| {horizon} | {metrics['n']} | {metrics['mape_pct']:.2f} | "
            f"{metrics['baseline_mape_pct']:.2f} | {metrics['coverage_pct']:.1f} |"
        )
    lines.extend([
        "",
        "## Сигналы BUY / WAIT",
        "",
        "| Сигнал | Решений | Успешных | Accuracy, % | Средняя выгода, % |",
        "|---|---:|---:|---:|---:|",
    ])
    for action, metrics in decisions["by_action"].items():
        if metrics["n"]:
            lines.append(
                f"| {action} | {metrics['n']} | {metrics['successes']} | "
                f"{metrics['accuracy_pct']:.1f} | {metrics['mean_benefit_pct']:+.2f} |"
            )
        else:
            lines.append(f"| {action} | 0 | 0 | — | — |")
    changed_forecasts = sum(r["predicted_price"] != r["anchor_price"] for r in forecast_records)
    buy = decisions["by_action"]["BUY"]
    wait = decisions["by_action"]["WAIT"]
    lines.extend([
        f"| **Всего** | **{decisions['n']}** | — | **{decisions['accuracy_pct']:.1f}** | **{decisions['mean_benefit_pct']:+.2f}** |",
        "",
        "### Интерпретация",
        "",
        (f"- Кривая сохранила текущую цену во всех {forecast['n']} парах и не улучшила baseline. "
         "На дальних датах вылета действует плато, поэтому проверка не подтверждает добавленную точность кривой."
         if changed_forecasts == 0 else
         f"- Кривая изменила текущую цену в {changed_forecasts} из {forecast['n']} пар; сравнение с baseline показано выше."),
        ("- BUY не встретился: модель на этом наборе всегда видит более низкую будущую цену." if not buy["n"] else
         f"- BUY: {buy['successes']} из {buy['n']} успешных, но средняя выгода {buy['mean_benefit_pct']:+.2f}%. "
         "Высокая accuracy скрывает редкую дорогую ошибку: смотреть нужно и на цену ошибки."
         if buy["mean_benefit_pct"] < 0 else
         f"- BUY: {buy['successes']} из {buy['n']} успешных, средняя выгода {buy['mean_benefit_pct']:+.2f}%."),
        (f"- WAIT: только {wait['successes']} из {wait['n']} успешных. Ожидание чаще не давало снижения минимум на "
         f"{STABLE_THRESHOLD_PCT:.0f}%; сигнал требует осторожности и дальнейшей проверки." if wait["n"] else "- WAIT не встретился."),
        "",
        "### Удачные и ошибочные решения",
        "",
        "Для BUY и WAIT отдельно показаны наиболее выгодный успешный и наиболее дорогой ошибочный случаи.",
        "",
        "| Сигнал | Маршрут | Вылет | Решение от | Цена | Будущий минимум | Изменение | Итог |",
        "|---|---|---|---|---:|---:|---:|---|",
    ])
    selected = []
    for action in ("BUY", "WAIT"):
        for success in (True, False):
            candidates = [r for r in decision_records if r["action"] == action and r["success"] == success]
            if candidates:
                selected.append((max if success else min)(candidates, key=lambda r: r["benefit_pct"]))
    lines.extend(case_row(record) for record in selected)
    lines.extend([
        "",
        "## Ограничения результата",
        "",
        "- История занимает пять календарных дней: проверяются только краткосрочные изменения, а не прогноз за недели и месяцы.",
        "- Наблюдения неравномерны и сосредоточены на нескольких маршрутах; выборка не представляет все направления.",
        "- Несколько пар относятся к одному маршруту и дате вылета, поэтому наблюдения не независимы; это диагностическая оценка, не статистическое доказательство.",
        "- Для оценки полного BUY/WAIT и длинного горизонта нужно продолжить сбор цен и архивировать новости, календарный сигнал и итоговый вердикт.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=Path, default=ROOT / "bench" / "data" / "price_history.json")
    parser.add_argument("--output", type=Path, default=ROOT / "bench" / "results" / "price_backtest")
    args = parser.parse_args()
    data = json.loads(args.history.read_text(encoding="utf-8"))
    groups = group_history(data["records"])
    forecast_records = evaluate_forecasts(groups)
    decision_records = evaluate_decisions(groups)
    forecast = forecast_summary(forecast_records)
    decisions = decision_summary(decision_records)
    payload = {
        "method": {
            "stable_threshold_pct": STABLE_THRESHOLD_PCT,
            "decision_scope": "forecast minimum from decision date to departure",
            "lookahead": False,
        },
        "data": {
            "snapshots": len(data["records"]),
            "keys": len(groups),
            "repeated_keys": sum(len(rows) > 1 for rows in groups.values()),
            "from": data["records"][0]["fetched_at"],
            "to": data["records"][-1]["fetched_at"],
        },
        "forecast": forecast,
        "decisions": decisions,
        "forecast_records": forecast_records,
        "decision_records": decision_records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    args.output.with_suffix(".md").write_text(
        to_markdown(data, forecast, decisions, forecast_records, decision_records), encoding="utf-8")
    print(f"Forecast pairs: {forecast['n']}; decisions: {decisions['n']}")
    print(f"Results: {args.output.with_suffix('.md')}")


if __name__ == "__main__":
    main()
