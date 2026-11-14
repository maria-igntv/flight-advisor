"""orchestrator — итоговый вердикт «когда покупать» из четырёх сигналов.

BUY/WAIT определяется прогнозом на период «сегодня → вылет»: BUY, если
будущий минимум не ниже текущей цены; WAIT, если модель видит более низкую
цену и называет предполагаемую дату. Прошлая динамика остаётся контекстом,
но сама по себе не является основанием ждать или покупать.

Каждый вердикт сопровождается списком сигналов с объяснениями —
принцип «объяснимый AI», никакого чёрного ящика (это же и демо-материал).
"""
from __future__ import annotations

# Насколько минимум диапазона должен быть ниже средней, чтобы считать
# конкретную дату «заметно выгоднее» остальных.
CHEAP_DISCOUNT_PCT = 3.0


def verdict(
    res: dict, news: dict | None, dyn: dict, busy: dict | None = None, forecast: dict | None = None
) -> dict:
    """Вердикт по календарю цен, прогнозу до вылета, новостям и занятости."""
    priced = [c for c in res.get("calendar", []) if c["price"] is not None]
    cheapest = res.get("cheapest_date")

    if not priced or not cheapest:
        return {
            "signal": "no_data",
            "title": "Недостаточно данных",
            "text": "Цены не получены — вердикт невозможен.",
            "confidence": "low",
            "signals": [],
        }

    mean_price = sum(c["price"] for c in priced) / len(priced)
    discount_pct = (mean_price - cheapest["price"]) / mean_price * 100

    signals: list[dict] = []

    # --- сигнал 1: календарь цен ------------------------------------------
    if len(priced) > 1 and discount_pct >= CHEAP_DISCOUNT_PCT:
        signals.append({
            "name": "📅 Календарь цен",
            "value": f"минимум на {cheapest['dep_date']} — на {discount_pct:.0f}% ниже средней по диапазону",
            "impact": "buy",
        })
    else:
        signals.append({
            "name": "📅 Календарь цен",
            "value": (
                f"цены по диапазону близки (минимум на {cheapest['dep_date']}, "
                f"разброс всего {discount_pct:.0f}%)"
                if len(priced) > 1
                else f"единственная дата с ценой — {cheapest['dep_date']}"
            ),
            "impact": "neutral",
        })

    # --- сигнал 2: динамика и прогноз до вылета ----------------------------
    signals.append({
        "name": "📈 Динамика цены",
        "value": dyn["text"],
        "impact": "neutral",
    })
    forecast_min = None
    if forecast and forecast.get("points"):
        future_points = forecast["points"][1:]
        if future_points:
            forecast_min = min(future_points, key=lambda point: point["price"])
    if forecast_min:
        impact = "wait" if forecast_min["price"] < cheapest["price"] else "buy"
        value = (
            f"прогноз до вылета: минимум {forecast_min['price']:,} {res['currency']} "
            f"{forecast_min['date']} против {cheapest['price']:,} сейчас"
        )
    else:
        impact = "neutral"
        value = "прогноз до вылета недоступен"
    signals.append({"name": "🔮 Прогноз до вылета", "value": value, "impact": impact})

    # --- сигнал 3: новости -------------------------------------------------
    if news:
        if news.get("risk_level") == "warning":
            titles = "; ".join(w["title"] for w in news.get("warnings", []))
            signals.append({
                "name": "📰 Новости региона",
                "value": f"опасные события: {titles} — возможны колебания цен и расписания",
                "impact": "careful",
            })
        else:
            signals.append({
                "name": "📰 Новости региона",
                "value": "опасных событий не обнаружено",
                "impact": "neutral",
            })

    # --- сигнал 4: занятость календаря -------------------------------------
    if busy is not None:
        day_events = busy.get(cheapest["dep_date"])
        if day_events:
            evs = "; ".join(f"{e['title']} ({e['time']})" for e in day_events)
            signals.append({
                "name": "🗓 Ваш календарь",
                "value": f"в день вылета {cheapest['dep_date']} встречи: {evs}",
                "impact": "careful",
            })
        else:
            signals.append({
                "name": "🗓 Ваш календарь",
                "value": f"на {cheapest['dep_date']} занятых встреч нет",
                "impact": "neutral",
            })

    # --- вердикт -----------------------------------------------------------
    score = sum(1 if s["impact"] == "buy" else -1 if s["impact"] == "wait" else 0
                for s in signals)
    careful = any(s["impact"] == "careful" for s in signals)

    if score > 0:
        signal, title = "BUY", f"Покупайте сейчас — на вылет {cheapest['dep_date']}"
        text = (
            f"По прогнозу на период с сегодняшнего дня до вылета цена не опустится ниже "
            f"{cheapest['price']:,} {res['currency']} или вырастет. Модель обучена на синтетике."
        )
    elif score < 0:
        signal, title = "WAIT", "Можно подождать — прогноз ниже текущей цены"
        text = (
            f"До вылета ожидается минимум {forecast_min['price']:,} {res['currency']} "
            f"предположительно {forecast_min['date']}. До этой даты можно не покупать, "
            "но прогноз обучен на синтетических данных и требует проверки на реальной истории."
        )
    else:
        signal, title = "WATCH", "Ситуация нейтральная — следим за ценой"
        text = (
            f"Разумный выбор — {cheapest['dep_date']} за {cheapest['price']:,} "
            f"{res['currency']}. Сильных сигналов «покупать срочно» или «ждать» нет."
        )

    if careful:
        text += " ⚠️ Есть предупреждения (новости региона и/или занятость в календаре) — см. сигналы ниже."

    return {
        "signal": signal,
        "title": title,
        "text": text,
        "cheapest_date": cheapest["dep_date"],
        "confidence": "medium" if dyn.get("status") == "ok" else "low",
        "signals": signals,
    }
