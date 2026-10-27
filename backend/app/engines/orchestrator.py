"""orchestrator — итоговый вердикт «когда покупать» из трёх сигналов.

Сигналы:
  1. Календарь цен — насколько минимум диапазона ниже средней (spread дат);
  2. Динамика цены — alerts_engine: растёт / падает / стабильно / collecting;
  3. Новости региона — risk_level из news_engine (катаклизмы/беспорядки);
  4. Календарь занятости — calendar_engine: встречи в день вылета (careful).

Вердикт:
  BUY   — покупайте сейчас, даты D: цена на минимуме и/или растёт;
  WAIT  — можно подождать: цена падает и не на минимуме диапазона;
  WATCH — сигналы нейтральны или данных мало: следим.

Каждый вердикт сопровождается списком сигналов с объяснениями —
принцип «объяснимый AI», никакого чёрного ящика (это же и демо-материал).
"""
from __future__ import annotations

# Насколько минимум диапазона должен быть ниже средней, чтобы считать
# конкретную дату «заметно выгоднее» остальных.
CHEAP_DISCOUNT_PCT = 3.0


def verdict(res: dict, news: dict | None, dyn: dict, busy: dict | None = None) -> dict:
    """res — результат price_engine.recommend(); news — news_engine.get_news();
    dyn — alerts_engine.dynamics() для самой дешёвой даты;
    busy — calendar_engine.busy_dates() (карта занятых дней диапазона) или None.
    """
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

    # --- сигнал 2: динамика -----------------------------------------------
    if dyn.get("status") == "ok":
        impact = {"растёт": "buy", "падает": "wait", "стабильно": "neutral"}[dyn["direction"]]
        signals.append({
            "name": "📈 Динамика цены",
            "value": dyn["text"],
            "impact": impact,
        })
    else:
        signals.append({
            "name": "📈 Динамика цены",
            "value": dyn["text"],
            "impact": "neutral",
        })

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
            f"Цена {cheapest['price']:,} {res['currency']} — заметно ниже соседних дат "
            "и/или растёт; дальше ждать обычно дороже."
        )
    elif score < 0:
        signal, title = "WAIT", "Можно подождать — цена снижается"
        text = (
            "Цена падает относительно накопленной истории: покупка ближе к минимуму "
            "может быть выгоднее. Следите за обновлениями каждые несколько часов."
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
