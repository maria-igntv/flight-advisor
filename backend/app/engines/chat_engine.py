"""AI-помощник с tool-use: сам запускает поиск цен
(инструмент search_prices -> price_engine.recommend), когда вопрос про
конкретный маршрут/даты, и отвечает на общие вопросы своими знаниями.

Провайдер:
- локально — Anthropic-гейтвей (ANTHROPIC_BASE_URL и ANTHROPIC_AUTH_TOKEN);
- веб-версия на Streamlit Cloud — YandexGPT (YANDEX_GPT_API_KEY и
  YANDEX_GPT_FOLDER_ID в Secrets), если гейтвей не задан.
"""
from __future__ import annotations

import json
import os
from datetime import date as _date, timedelta as _timedelta

from anthropic import Anthropic

from . import yandex_gpt

_ANTHROPIC_TOKEN = "ANTHROPIC" + "_AUTH_TOKEN"
_MISSING_LLM = (
    "LLM не настроен. Веб-версия использует YandexGPT: в Secrets приложения "
    "Streamlit Cloud укажите YANDEX_GPT_API_KEY (API-ключ сервисного аккаунта "
    "Yandex Cloud) и YANDEX_GPT_FOLDER_ID (идентификатор каталога). "
    "Локальный запуск использует ANTHROPIC_BASE_URL и ANTHROPIC_AUTH_TOKEN."
)

SYSTEM_PROMPT = """\
Ты — AI-помощник платформы Flight Ticket Advisor. Платформа показывает \
календарь цен на авиабилеты (Google Flights) и рекомендует, когда выгоднее покупать.

Правила:
1. Отвечай на ЛЮБОЙ вопрос своими знаниями — НЕ привязывайся к открытому на \
платформе поиску. Если спрашивают про другой маршрут/страну — отвечай по своим \
знаниям, а не отсылай к платформе.
2. Если вопрос про стоимость конкретного перелёта («сколько стоит слетать…») — \
вызывай инструмент search_prices с нужным маршрутом/датами/составом пассажиров \
и отвечай РЕАЛЬНЫМИ цифрами из результата. Даты интерпретируй сам: \
«на Рождество» ≈ вылет 24–30 декабря, «на неделю» — возвращение через 7 дней, \
«в сезон сакуры» ≈ конец марта – начало апреля.
3. На вопросы «когда покупать» отвечай своими знаниями (сезонность, типичный \
рост цен ближе к вылету) и подкрепляй данными инструмента, если они есть.
4. Если search_prices не вернул цен по маршруту — честно скажи об этом и дай \
оценку по своим знаниям, с оговоркой.
Отвечай по-русски (или на языке вопроса), кратко и по делу, с конкретными числами.\
"""


def _anthropic_ready() -> bool:
    return bool(os.environ.get("ANTHROPIC_BASE_URL") and os.environ.get(_ANTHROPIC_TOKEN))


def provider() -> str | None:
    """anthropic, yandex или None, если ключей нет."""
    if _anthropic_ready():
        return "anthropic"
    if yandex_gpt.ready():
        return "yandex"
    return None


def configuration_error() -> str | None:
    if provider():
        return None
    return _MISSING_LLM


def _client() -> Anthropic:
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    api_key = os.environ.get(_ANTHROPIC_TOKEN)
    if not base_url or not api_key:
        raise RuntimeError(_MISSING_LLM)
    return Anthropic(base_url=base_url, api_key=api_key)


def _model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")


def build_context(last_result: dict | None) -> str:
    """Контекст последнего поиска цен (+ новости региона) для LLM."""
    if not last_result:
        return "(пользователь ещё не делал поиск на платформе)"
    cal = [
        {"date": c["dep_date"], "min_price": c["price"]}
        for c in last_result.get("calendar", [])
    ]
    ctx = {
        "route": f"{last_result.get('origin')} -> {last_result.get('dest')}",
        "currency": last_result.get("currency"),
        "return_date": last_result.get("return_date"),
        "passengers": last_result.get("passengers"),
        "price_calendar": cal,
        "cheapest": last_result.get("cheapest_date"),
    }
    news = last_result.get("news")
    if news:
        ctx["region_news"] = {
            "region": news.get("region"),
            "digest": [
                {"title": d["title"], "source": d["source"], "risk": d.get("risk")}
                for d in news.get("digest", [])
            ],
            "risk_level": news.get("risk_level"),
        }
    if last_result.get("verdict"):
        v = last_result["verdict"]
        ctx["verdict"] = {
            "signal": v.get("signal"),
            "title": v.get("title"),
            "text": v.get("text"),
            "signals": [
                {"name": s["name"], "value": s["value"], "impact": s["impact"]}
                for s in v.get("signals", [])
            ],
        }
    if last_result.get("dynamics"):
        d = last_result["dynamics"]
        ctx["price_dynamics"] = {
            "status": d.get("status"),
            "direction": d.get("direction"),
            "delta_pct": d.get("delta_pct"),
            "measurements": d.get("measurements"),
        }
    return json.dumps(ctx, ensure_ascii=False)


# Инструмент поиска цен: модель вызывает его сама, когда вопрос про
# конкретный перелёт — включая маршруты, не открытые на платформе.
_TOOLS = [
    {
        "name": "search_prices",
        "description": (
            "Поиск реальных цен на авиабилеты (Google Flights) по маршруту и датам. "
            "Вызывай, когда пользователь спрашивает про стоимость перелёта — даже если "
            "это НЕ тот маршрут, что открыт на платформе. "
            "origin/dest — IATA-коды города или аэропорта "
            "(MOW=Москва, LED=Санкт-Петербург, NYC=Нью-Йорк, TYO=Токио, PAR=Париж, "
            "BKK=Бангкок, IST=Стамбул, DXB=Дубай, BAX=Барнаул и т.п.). "
            "dep_date — дата вылета YYYY-MM-DD; dep_end — конец диапазона дат вылета "
            "(необязательно, диапазон не шире 10 дней); return_date — YYYY-MM-DD для "
            "туда-обратно. Возвращает min-цену на каждую дату (за ВСЕХ пассажиров)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "IATA-код вылета, напр. MOW"},
                "dest": {"type": "string", "description": "IATA-код прилёта, напр. NYC"},
                "dep_date": {"type": "string", "description": "YYYY-MM-DD"},
                "dep_end": {"type": "string", "description": "YYYY-MM-DD, необязательно"},
                "return_date": {"type": "string", "description": "YYYY-MM-DD, необязательно"},
                "adults": {"type": "integer", "description": "взрослые 12+, по умолчанию 1"},
                "children": {"type": "integer", "description": "дети 2–11, по умолчанию 0"},
                "infants": {"type": "integer", "description": "младенцы до 2, по умолчанию 0"},
                "currency": {"type": "string", "enum": ["RUB", "USD", "EUR"]},
            },
            "required": ["origin", "dest", "dep_date"],
        },
    }
]

# Максимум раундов «модель вызвала инструмент -> получили результат -> снова спросили».
_MAX_TOOL_ROUNDS = 3


def _run_search(inp: dict) -> str:
    """Исполнение search_prices: recommend() -> компактный JSON для модели."""
    from .price_engine import recommend  # локальный импорт: избежать цикла

    def _day(v, default=None):
        if v in (None, ""):
            return default
        try:
            return _date.fromisoformat(str(v)[:10])
        except ValueError:
            return default

    origin = str(inp.get("origin", "")).strip().upper()
    dest = str(inp.get("dest", "")).strip().upper()
    dep = _day(inp.get("dep_date"))
    today = _date.today()
    if dep is not None and dep < today:
        return json.dumps(
            {"error": f"dep_date {dep.isoformat()} is in the past (today "
                      f"{today.isoformat()}). Request future dates."},
            ensure_ascii=False,
        )
    if not origin or not dest or dep is None:
        return json.dumps(
            {"error": "Нужны origin, dest (IATA) и dep_date (YYYY-MM-DD)."},
            ensure_ascii=False,
        )
    dep_end = _day(inp.get("dep_end"), dep)
    # Диапазон ограничиваем: один запрос к Google ~3-5 сек на дату.
    if (dep_end - dep).days > 9:
        dep_end = dep + _timedelta(days=9)
    ret = _day(inp.get("return_date"))
    if ret is not None and ret <= dep:
        ret = None
    adults = int(inp.get("adults") or 1)
    children = int(inp.get("children") or 0)
    infants = int(inp.get("infants") or 0)
    currency = str(inp.get("currency") or "RUB").upper()

    res = recommend(
        origin, dest, dep, dep_end, currency,
        return_date=ret, adults=adults, children=children, infants=infants,
    )
    cal = [
        {"date": c["dep_date"], "min_price": c["price"]}
        for c in res["calendar"]
    ]
    cheapest = res.get("cheapest_date")
    out = {
        "route": f"{origin}->{dest}",
        "currency": currency,
        "return_date": ret.isoformat() if ret else None,
        "passengers": {"adults": adults, "children": children, "infants": infants},
        "price_calendar": cal,
        "cheapest_date": {"date": cheapest["dep_date"], "price": cheapest["price"]} if cheapest else None,
    }
    if not cheapest:
        out["note"] = (
            "Источник не вернул цен по этому маршруту (рейсы могут быть, но перевозчик "
            "не отдаёт цены в Google Flights)."
        )
    return json.dumps(out, ensure_ascii=False)


DIGEST_SYSTEM = (
    "Ты — редактор travel-платформы. Сделай краткое саммари новостей "
    "региона назначения для путешественника: 2–4 предложения на русском, "
    "по-человечески, без маркдауна и без перечисления всех заголовков. "
    "Отметь тревожные события (⚠️), если они есть, и их возможное влияние "
    "на поездку. Пиши только по фактам из списка, ничего не выдумывай."
)


def digest_prompt(news: dict, region: str) -> str:
    """User-сообщение для саммари дайджеста; пустая строка, если новостей нет."""
    digest = [d for d in (news or {}).get("digest", []) if d.get("title")]
    if not digest:
        return ""
    items = "\n".join(
        f"- [{d.get('source', '')}] {d['title']}"
        + (" ⚠️ тревожное событие" if d.get("risk") else "")
        for d in digest
    )
    return f"Регион: {region}.\nНовости:\n{items}"


def summarize_digest(news: dict, region: str) -> str:
    """Короткое саммари новостного дайджеста региона на русском (2–4 фразы).

    Вызывается при раскрытии раздела новостей на странице поиска.
    Возвращает пустую строку, если дайджест пуст; бросает исключение
    наверх при недоступности LLM (UI покажет только список ссылок).
    """
    user = digest_prompt(news, region)
    if not user:
        return ""
    if provider() == "yandex":
        return yandex_gpt.complete_text(DIGEST_SYSTEM, user, max_tokens=400).strip()
    resp = _client().messages.create(
        model=_model(),
        max_tokens=400,
        system=DIGEST_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def _system_prompt(last_result: dict | None, today: _date | None = None) -> str:
    today = today or _date.today()
    return (
        f"{SYSTEM_PROMPT}\n\nСегодня: "
        f"{today.strftime('%d.%m.%Y')} ({today.isoformat()}). "
        "Все «праздники/сезоны» в вопросах пользователя относи к БУДУЩИМ датам "
        "относительно сегодняшней.\n\n"
        "Контекст последнего поиска на платформе (используй ТОЛЬКО если он "
        "релевантен вопросу пользователя):\n"
        + build_context(last_result)
    )


def _yandex_tools() -> list[dict]:
    return [
        {
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            }
        }
        for tool in _TOOLS
    ]


def _execute_tool(name: str, arguments: dict) -> str:
    if name == "search_prices":
        try:
            return _run_search(arguments)
        except Exception as e:  # noqa: BLE001 — ошибку возвращаем модели
            return f"Поиск не удался: {e}"
    return f"Неизвестный инструмент: {name}"


def _ask_yandex(question: str, history: list[dict], last_result: dict | None) -> str:
    messages: list[dict] = [{"role": "system", "text": _system_prompt(last_result)}]
    for m in history:
        messages.append({"role": m["role"], "text": m["content"]})
    messages.append({"role": "user", "text": question})
    tools = _yandex_tools()
    text = ""
    for _round in range(_MAX_TOOL_ROUNDS + 1):
        turn = yandex_gpt.complete(messages, tools=tools, max_tokens=2048)
        text = turn.text
        if not turn.tool_calls:
            return text or "Не удалось получить ответ модели. Попробуйте переформулировать вопрос."
        results = [
            {"name": call["name"], "content": _execute_tool(call["name"], call["arguments"])}
            for call in turn.tool_calls
        ]
        assistant: dict = {
            "role": "assistant",
            "toolCallList": {
                "toolCalls": [
                    {"functionCall": {"name": call["name"], "arguments": call["arguments"]}}
                    for call in turn.tool_calls
                ]
            },
        }
        if turn.text:
            assistant["text"] = turn.text
        messages.append(assistant)
        messages.append({
            "role": "user",
            "toolResultList": {
                "toolResults": [
                    {"functionResult": {"name": item["name"], "content": item["content"]}}
                    for item in results
                ]
            },
        })
    return text or "Не удалось получить ответ модели. Попробуйте переформулировать вопрос."


def ask(question: str, history: list[dict], last_result: dict | None = None) -> str:
    """Вопрос к LLM с tool-use: помощник сам может запустить поиск цен.

    history: [{'role': 'user'|'assistant', 'content': str}, ...]
    last_result: результат recommend() — контекст открытого на платформе
    поиска (используется моделью только если релевантен вопросу).
    """
    active = provider()
    if active is None:
        raise RuntimeError(_MISSING_LLM)
    if active == "yandex":
        return _ask_yandex(question, history, last_result)
    client = _client()
    system = _system_prompt(last_result)
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": question})

    for _round in range(_MAX_TOOL_ROUNDS + 1):
        resp = client.messages.create(
            model=_model(),
            max_tokens=2048,
            system=system,
            messages=messages,
            tools=_TOOLS,
        )
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text")

        # Модель запросила инструмент: исполняем и возвращаем результат.
        tool_results = []
        for b in resp.content:
            if b.type != "tool_use":
                continue
            if b.name == "search_prices":
                try:
                    content = _run_search(b.input)
                    is_error = False
                except Exception as e:  # noqa: BLE001 — ошибку возвращаем модели
                    content = f"Поиск не удался: {e}"
                    is_error = True
            else:
                content = f"Неизвестный инструмент: {b.name}"
                is_error = True
            tool_results.append(
                {"type": "tool_result", "tool_use_id": b.id, "content": content,
                 **({"is_error": True} if is_error else {})}
            )
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": b.text}
                    if b.type == "text"
                    # thinking/tool_use: отдаём как есть (у gateway-моделей
                    # бывают thinking-блоки — их нужно пересылать обратно)
                    else b.model_dump()
                    for b in resp.content
                ],
            }
        )
        messages.append({"role": "user", "content": tool_results})
    return "".join(b.text for b in resp.content if b.type == "text")
