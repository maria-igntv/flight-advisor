"""CatBoost для прогноза цены от даты покупки до будущей даты на синтетике.

Запуск: .venv/bin/python -m bench.train_synthetic_price_model
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from catboost import CatBoostRegressor

ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT / "backend" / "app" / "data" / "price_model.cbm"
META_PATH = ROOT / "backend" / "app" / "data" / "price_model.json"
FEATURES = [
    "forecast_horizon_days", "days_before_departure", "purchase_day_of_week",
    "purchase_month", "purchase_hour", "departure_day_of_week", "departure_month",
]
ROUTES = [
    ("MOW", "TYO", 52000, 0.025), ("MOW", "IST", 22000, 0.018),
    ("MOW", "DXB", 31000, 0.022), ("MOW", "PAR", 48000, 0.020),
    ("MOW", "LON", 57000, 0.028), ("MOW", "BKK", 43000, 0.024),
    ("LED", "IST", 24000, 0.019), ("LED", "DXB", 33000, 0.021),
]
HORIZONS = (1, 3, 7, 14, 30)


def booking_multiplier(days: int) -> float:
    if days > 180:
        return 1.04 - 0.00015 * min(days - 180, 150)
    if days > 60:
        return 0.96 + 0.0008 * (180 - days)
    if days > 30:
        return 0.96 + 0.0018 * (60 - days)
    if days > 14:
        return 0.98 + 0.006 * (30 - days)
    return 1.08 + 0.025 * (14 - days)


def generate_observations(seed: int = 42) -> dict[tuple, list[dict]]:
    rng = random.Random(seed)
    start = datetime(2025, 1, 1, 0, 0)
    series = {}
    for origin, dest, base_price, volatility in ROUTES:
        for departure_offset in (45, 90, 135, 180, 225, 270, 315):
            departure = start + timedelta(days=departure_offset, hours=12)
            observations = []
            noise = 0.0
            for step in range(departure_offset * 4):
                purchase = start + timedelta(hours=step * 6)
                days = departure_offset - step // 4
                noise = 0.82 * noise + rng.gauss(0, volatility / 3)
                weekly = 1.012 if purchase.weekday() >= 5 else 0.998
                hourly = 1 + 0.012 * math.sin((purchase.hour / 24) * 2 * math.pi)
                seasonal = 1 + 0.025 * math.sin((purchase.month / 12) * 2 * math.pi)
                departure_week = 1.018 if departure.weekday() >= 5 else 0.995
                price = round(base_price * booking_multiplier(days) * weekly * hourly
                              * seasonal * departure_week * (1 + noise))
                observations.append({"at": purchase, "days_before": days, "price": price})
            series[(origin, dest, departure)] = observations
    return series


def make_pairs(series: dict[tuple, list[dict]]) -> list[dict]:
    rows = []
    for (origin, dest, departure), observations in series.items():
        for anchor_index, anchor in enumerate(observations):
            for horizon in HORIZONS:
                target_index = anchor_index + horizon * 4
                if target_index >= len(observations):
                    continue
                target = observations[target_index]
                purchase = anchor["at"]
                rows.append({
                    "origin": origin, "dest": dest,
                    "purchase_at": purchase.isoformat(),
                    "target_at": target["at"].isoformat(),
                    "departure_at": departure.isoformat(),
                    "current_price": anchor["price"],
                    "forecast_horizon_days": horizon,
                    "days_before_departure": target["days_before"],
                    "purchase_day_of_week": purchase.weekday(),
                    "purchase_month": purchase.month,
                    "purchase_hour": purchase.hour,
                    "departure_day_of_week": departure.weekday(),
                    "departure_month": departure.month,
                    "target_price": target["price"],
                    "target_change_pct": (target["price"] - anchor["price"]) / anchor["price"] * 100,
                })
    return rows


def metrics(rows: list[dict], predictions) -> dict:
    actual = np.array([r["target_price"] for r in rows], dtype=float)
    predicted = np.asarray(predictions, dtype=float)
    baseline = np.array([r["current_price"] for r in rows], dtype=float)
    mse = np.mean((actual - predicted) ** 2)
    return {
        "n": len(rows),
        "mae": float(np.mean(np.abs(actual - predicted))),
        "rmse": float(math.sqrt(mse)),
        "mape_pct": float(np.mean(np.abs(actual - predicted) / actual) * 100),
        "r2": float(1 - mse / np.mean((actual - np.mean(actual)) ** 2)),
        "baseline_mape_pct": float(np.mean(np.abs(actual - baseline) / actual) * 100),
        "baseline_rmse": float(math.sqrt(np.mean((actual - baseline) ** 2))),
    }


def split_by_time(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict], str]:
    all_dates = sorted({r["purchase_at"] for r in rows})
    train_end = all_dates[int(len(all_dates) * 0.7)]
    valid_end = all_dates[int(len(all_dates) * 0.85)]
    train = [r for r in rows if r["purchase_at"] <= train_end]
    valid = [r for r in rows if train_end < r["purchase_at"] <= valid_end]
    test = [r for r in rows if r["purchase_at"] > valid_end]
    return train, valid, test, valid_end


def main() -> None:
    observations = generate_observations()
    rows = make_pairs(observations)
    train, valid, test, test_start = split_by_time(rows)
    model = CatBoostRegressor(
        iterations=450, depth=7, learning_rate=0.06, loss_function="RMSE",
        random_seed=42, verbose=False, allow_writing_files=False,
    )
    model.fit(
        [[r[f] for f in FEATURES] for r in train],
        [r["target_change_pct"] for r in train],
        eval_set=(
            [[r[f] for f in FEATURES] for r in valid],
            [r["target_change_pct"] for r in valid],
        ),
        early_stopping_rounds=45,
    )
    predicted_change = model.predict([[r[f] for f in FEATURES] for r in test])
    predictions = np.array([r["current_price"] for r in test]) * (1 + predicted_change / 100)
    result = metrics(test, predictions)
    residuals = (np.array([r["target_price"] for r in test]) - predictions) / predictions * 100
    result["residual_p05_pct"] = float(np.percentile(residuals, 5))
    result["residual_p95_pct"] = float(np.percentile(residuals, 95))
    importance = sorted(
        zip(FEATURES, model.get_feature_importance()), key=lambda item: item[1], reverse=True
    )
    examples = sorted(
        [
            {
                **{k: r[k] for k in ("origin", "dest", "purchase_at", "departure_at", "current_price", "target_price")},
                "predicted_price": round(float(prediction)),
                "abs_pct_error": abs(float(prediction) - r["target_price"]) / r["target_price"] * 100,
            }
            for r, prediction in zip(test, predictions)
        ],
        key=lambda r: r["abs_pct_error"], reverse=True,
    )
    metadata = {
        "model": "CatBoostRegressor",
        "version": "synthetic-v3",
        "trained_on": "synthetic",
        "target": "future_price_change_pct",
        "features": FEATURES,
        "horizons_days": HORIZONS,
        "seed": 42,
        "residual_p05_pct": result["residual_p05_pct"],
        "residual_p95_pct": result["residual_p95_pct"],
    }
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(MODEL_PATH)
    META_PATH.write_text(json.dumps(metadata, ensure_ascii=False, indent=1), encoding="utf-8")
    payload = {
        "method": {
            "data": "synthetic", "rows": len(rows), "train": len(train),
            "valid": len(valid), "test": len(test), "test_start": test_start,
            "split": "chronological by purchase date 70/15/15", "lookahead": False,
            "real_world_performance_claim": False,
        },
        "metrics": result,
        "feature_importance": {k: round(float(v), 2) for k, v in importance},
        "best_examples": list(reversed(examples[-3:])),
        "worst_examples": examples[:3],
    }
    output = ROOT / "bench" / "results" / "synthetic_price_model"
    output.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    md = f"""# CatBoost на синтетических ценах

Модель обучена только на синтетических данных. Метрики проверяют воспроизводимость и способность выучить закономерности генератора, **не реальную точность прогноза авиабилетов**.

## Данные и постановка

- {len(rows)} пар «текущая дата → будущая дата», 8 маршрутов, 7 дат вылета на маршрут, полный годовой цикл.
- Горизонты: 1, 3, 7, 14, 30 дней. Модель предсказывает изменение цены в %, стоимость восстанавливается от текущей цены.
- Признаки: горизонт, дней до вылета, день/месяц/час покупки, день недели и месяц вылета.
- Хронологический split по дате начала прогноза 70/15/15: train={len(train)}, validation={len(valid)}, test={len(test)}; test начинается с {test_start}.
- Baseline: текущая цена сохраняется. Seed 42; команда: `.venv/bin/python -m bench.train_synthetic_price_model`.

## Результат на отложенном будущем срезе

| Метрика | CatBoost | Baseline |
|---|---:|---:|
| MAE, ₽ | {result['mae']:.0f} | — |
| RMSE, ₽ | {result['rmse']:.0f} | {result['baseline_rmse']:.0f} |
| MAPE, % | {result['mape_pct']:.2f} | {result['baseline_mape_pct']:.2f} |
| R² | {result['r2']:.4f} | — |

90% эмпирического интервала ошибок: {result['residual_p05_pct']:+.2f}% … {result['residual_p95_pct']:+.2f}%.

## Важность признаков

| Признак | Важность, % |
|---|---:|
"""
    md += "".join(f"| {name} | {value:.2f} |\n" for name, value in importance)
    md += "\n## Наибольшие ошибки\n\n| Маршрут | Покупка | Факт | Прогноз | Ошибка, % |\n|---|---|---:|---:|---:|\n"
    md += "".join(
        f"| {r['origin']}→{r['dest']} | {r['purchase_at'][:16]} | {r['target_price']} | "
        f"{r['predicted_price']} | {r['abs_pct_error']:.2f} |\n"
        for r in examples[:3]
    )
    md += "\n## Ограничения\n\n- Синтетические закономерности могут не совпадать с реальным рынком.\n- Нужен реальный исторический датасет минимум за 5–6 месяцев для обучения и внешней проверки.\n- До этого модель — демонстрация ML-конвейера, а не доказанный финансовый совет.\n"
    output.with_suffix(".md").write_text(md, encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Model: {MODEL_PATH}")


if __name__ == "__main__":
    main()
