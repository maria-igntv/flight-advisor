"""Синтетический датасет для сравнения LLM-провайдеров AI-помощника.

Все кейсы считаются от фиксированной «сегодняшней» даты TODAY, чтобы
результаты были воспроизводимы независимо от дня запуска.

Четыре задачи — ровно то, что помощник делает в продукте:
  tool      — вопрос про цену → вызвать search_prices с правильными аргументами;
  no_tool   — общий travel-вопрос → ответить знаниями, НЕ дёргая поиск;
  grounding — по результату search_prices назвать реальную минимальную цену
              и дату (без выдуманных цифр); пустой результат — честно сказать;
  digest    — саммари новостей региона: 2–4 предложения, без маркдауна,
              тревожные события отмечены.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

TODAY = date(2026, 10, 27)  # вторник

# Допустимые IATA: код города или любого его аэропорта.
CITY = {
    "MOW": {"MOW", "SVO", "DME", "VKO", "ZIA"},
    "LED": {"LED"},
    "IST": {"IST", "SAW"},
    "TYO": {"TYO", "NRT", "HND"},
    "NYC": {"NYC", "JFK", "EWR", "LGA"},
    "DXB": {"DXB", "DWC"},
    "BKK": {"BKK", "DMK"},
    "PAR": {"PAR", "CDG", "ORY"},
    "AER": {"AER"},
    "BAX": {"BAX"},
    "EVN": {"EVN"},
    "BJS": {"BJS", "PEK", "PKX"},
    "LON": {"LON", "LHR", "LGW", "STN", "LTN", "LCY"},
    "KZN": {"KZN"},
    "SVX": {"SVX"},
    "HKT": {"HKT"},
    "TBS": {"TBS"},
    "AYT": {"AYT"},
}


def _d(s: str) -> date:
    return date.fromisoformat(s)


# expect: origin/dest — ключ CITY; dep — точная дата или (lo, hi);
# ret — None (в одну сторону), точная дата, ("offset", lo, hi) — дней после
# вылета; dep_end — точная дата конца диапазона (если задан диапазон);
# adults/children/infants; currency (если явно просили).
TOOL_CASES: list[dict] = [
    {"id": "t01", "q": "Сколько стоит билет из Москвы в Стамбул на 15 декабря?",
     "expect": {"origin": "MOW", "dest": "IST", "dep": "2026-12-15", "ret": None}},
    {"id": "t02", "q": "Цена перелёта Санкт-Петербург — Сочи 3 января 2027, один взрослый.",
     "expect": {"origin": "LED", "dest": "AER", "dep": "2027-01-03", "ret": None, "adults": 1}},
    {"id": "t03", "q": "Сколько стоит слетать в Токио из Москвы туда-обратно: вылет 20 марта 2027, обратно 4 апреля 2027?",
     "expect": {"origin": "MOW", "dest": "TYO", "dep": "2027-03-20", "ret": "2027-04-04"}},
    {"id": "t04", "q": "Хотим вдвоём в Дубай из Москвы 10 января 2027 на неделю, сколько это будет стоить?",
     "expect": {"origin": "MOW", "dest": "DXB", "dep": "2027-01-10", "ret": ("offset", 6, 8), "adults": 2}},
    {"id": "t05", "q": "Сколько стоит билет Москва–Бангкок 5 февраля 2027 на двух взрослых и ребёнка 7 лет?",
     "expect": {"origin": "MOW", "dest": "BKK", "dep": "2027-02-05", "adults": 2, "children": 1}},
    {"id": "t06", "q": "Летим семьёй: я, жена, сын 4 года и дочка 1 год. Москва — Анталья 1 июня 2027, сколько стоят билеты?",
     "expect": {"origin": "MOW", "dest": "AYT", "dep": "2027-06-01", "adults": 2, "children": 1, "infants": 1}},
    {"id": "t07", "q": "Сколько стоит слетать вдвоём в Нью-Йорк на Рождество на неделю?",
     "expect": {"origin": "MOW", "dest": "NYC", "dep": ("2026-12-20", "2026-12-30"), "ret": ("offset", 6, 8), "adults": 2}},
    {"id": "t08", "q": "Посмотри цены Москва → Париж на майские 2027, туда и обратно на 5 дней.",
     "expect": {"origin": "MOW", "dest": "PAR", "dep": ("2027-04-28", "2027-05-05"), "ret": ("offset", 4, 6)}},
    {"id": "t09", "q": "Билет из Москвы в Барнаул на 30 ноября сколько стоит?",
     "expect": {"origin": "MOW", "dest": "BAX", "dep": "2026-11-30", "ret": None}},
    {"id": "t10", "q": "Сколько стоит перелёт Москва — Ереван в эту пятницу?",
     "expect": {"origin": "MOW", "dest": "EVN", "dep": "2026-10-30", "ret": None}},
    {"id": "t11", "q": "Сколько будет стоить билет из Москвы в Пекин через две недели?",
     "expect": {"origin": "MOW", "dest": "BJS", "dep": ("2026-11-09", "2026-11-11"), "ret": None}},
    {"id": "t12", "q": "Найди самый дешёвый день для вылета Москва — Стамбул с 10 по 15 декабря.",
     "expect": {"origin": "MOW", "dest": "IST", "dep": "2026-12-10", "dep_end": "2026-12-15", "ret": None}},
    {"id": "t13", "q": "Сколько стоит в одну сторону Казань — Екатеринбург 14 ноября?",
     "expect": {"origin": "KZN", "dest": "SVX", "dep": "2026-11-14", "ret": None}},
    {"id": "t14", "q": "What is the price of a flight from Moscow to London on 24 November 2026?",
     "expect": {"origin": "MOW", "dest": "LON", "dep": "2026-11-24", "ret": None}},
    {"id": "t15", "q": "Сколько стоят билеты в Токио в сезон сакуры из Москвы, туда-обратно на 10 дней?",
     "expect": {"origin": "MOW", "dest": "TYO", "dep": ("2027-03-15", "2027-04-10"), "ret": ("offset", 9, 11)}},
    {"id": "t16", "q": "Сколько в долларах стоит билет Москва–Пхукет на 20 декабря?",
     "expect": {"origin": "MOW", "dest": "HKT", "dep": "2026-12-20", "ret": None, "currency": "USD"}},
    {"id": "t17", "q": "Мы с мужем хотим в Тбилиси из Питера на Новый год: вылет 30 декабря, вернуться 3 января. Сколько стоит?",
     "expect": {"origin": "LED", "dest": "TBS", "dep": "2026-12-30", "ret": "2027-01-03", "adults": 2}},
    {"id": "t18", "q": "Сколько стоит перелёт Москва — Сочи на 8 марта 2027 для троих взрослых?",
     "expect": {"origin": "MOW", "dest": "AER", "dep": "2027-03-08", "ret": None, "adults": 3}},
    {"id": "t19", "q": "Нужен билет Москва — Стамбул на завтра, сколько стоит?",
     "expect": {"origin": "MOW", "dest": "IST", "dep": "2026-10-28", "ret": None}},
    {"id": "t20", "q": "Сколько стоит слетать из Москвы в Дубай на выходные: туда 14 ноября, обратно 15 ноября?",
     "expect": {"origin": "MOW", "dest": "DXB", "dep": "2026-11-14", "ret": "2026-11-15"}},
]

NO_TOOL_CASES: list[dict] = [
    {"id": "n01", "q": "Нужна ли гражданам РФ виза в Японию?"},
    {"id": "n02", "q": "Что посмотреть в Стамбуле за 3 дня?"},
    {"id": "n03", "q": "Когда в Токио цветёт сакура?"},
    {"id": "n04", "q": "Почему билеты дорожают ближе к дате вылета?"},
    {"id": "n05", "q": "Какой багаж обычно можно провезти в эконом-классе?"},
    {"id": "n06", "q": "За сколько месяцев до вылета обычно выгоднее покупать билеты в Азию?"},
    {"id": "n07", "q": "Какая погода в Дубае в январе?"},
    {"id": "n08", "q": "Что лучше: прямой рейс или с пересадкой в Стамбуле?"},
    {"id": "n09", "q": "Как добраться из аэропорта Нарита в центр Токио?"},
    {"id": "n10", "q": "Какие документы нужны ребёнку для вылета за границу?"},
]


def _calendar(seed: int, start: str, days: int, base: int) -> list[dict]:
    rnd = random.Random(seed)
    d0 = _d(start)
    return [
        {"date": (d0 + timedelta(days=i)).isoformat(),
         "min_price": int(base * rnd.uniform(0.85, 1.25)) // 10 * 10 + rnd.randint(1, 9)}
        for i in range(days)
    ]


def _grounding_case(cid: str, q: str, args: dict, base: int | None, days: int = 5,
                    currency: str = "RUB") -> dict:
    if base is None:
        cal = [{"date": args["dep_date"], "min_price": None}]
        cheapest = None
    else:
        cal = _calendar(int(cid[1:]) * 7919, args["dep_date"], days, base)
        cheapest = min(cal, key=lambda c: c["min_price"])
    result = {
        "route": f"{args['origin']}->{args['dest']}",
        "currency": currency,
        "return_date": args.get("return_date"),
        "passengers": {"adults": args.get("adults", 1), "children": 0, "infants": 0},
        "price_calendar": cal,
        "cheapest_date": ({"date": cheapest["date"], "price": cheapest["min_price"]}
                          if cheapest else None),
    }
    if cheapest is None:
        result["note"] = ("Источник не вернул цен по этому маршруту (рейсы могут быть, "
                          "но перевозчик не отдаёт цены в Google Flights).")
    return {"id": cid, "q": q, "args": args, "result": result,
            "cheapest": ({"date": cheapest["date"], "price": cheapest["min_price"]}
                         if cheapest else None)}


GROUNDING_CASES: list[dict] = [
    _grounding_case("g01", "Сколько стоит билет Москва — Стамбул 10–14 декабря? Какой день самый дешёвый?",
                    {"origin": "MOW", "dest": "IST", "dep_date": "2026-12-10", "dep_end": "2026-12-14"}, 24_000),
    _grounding_case("g02", "Когда дешевле всего вылететь из Москвы в Токио с 20 по 26 марта 2027?",
                    {"origin": "MOW", "dest": "TYO", "dep_date": "2027-03-20", "dep_end": "2027-03-26"}, 46_000, days=7),
    _grounding_case("g03", "Сколько стоит вдвоём туда-обратно Москва — Дубай, вылет 10–13 января 2027, обратно 20 января?",
                    {"origin": "MOW", "dest": "DXB", "dep_date": "2027-01-10", "dep_end": "2027-01-13",
                     "return_date": "2027-01-20", "adults": 2}, 98_000, days=4),
    _grounding_case("g04", "Подбери самый дешёвый вылет Санкт-Петербург — Сочи с 2 по 6 января 2027.",
                    {"origin": "LED", "dest": "AER", "dep_date": "2027-01-02", "dep_end": "2027-01-06"}, 11_500),
    _grounding_case("g05", "Сколько стоит Москва — Нью-Йорк 21–25 декабря?",
                    {"origin": "MOW", "dest": "NYC", "dep_date": "2026-12-21", "dep_end": "2026-12-25"}, 71_000),
    _grounding_case("g06", "Москва — Бангкок, вылет с 5 по 9 февраля 2027 — когда дешевле?",
                    {"origin": "MOW", "dest": "BKK", "dep_date": "2027-02-05", "dep_end": "2027-02-09"}, 39_000),
    _grounding_case("g07", "Сколько стоит билет из Москвы в Барнаул на 30 ноября?",
                    {"origin": "MOW", "dest": "BAX", "dep_date": "2026-11-30"}, None, days=1),
    _grounding_case("g08", "Сколько стоит перелёт Москва — Ереван 13–17 ноября?",
                    {"origin": "MOW", "dest": "EVN", "dep_date": "2026-11-13", "dep_end": "2026-11-17"}, 14_000),
]

DIGEST_CASES: list[dict] = [
    {"id": "d01", "region": "Япония · Токио", "risk": True, "risk_words": ["землетряс", "толчк", "сейсм"],
     "digest": [
         {"source": "The Guardian", "title": "Magnitude 6.8 earthquake strikes off Tokyo coast, trains halted", "risk": "катаклизмы"},
         {"source": "The Guardian", "title": "Japan’s cherry blossom forecast: early bloom expected in March"},
         {"source": "ТАСС", "title": "Японские авиакомпании увеличат число рейсов в Европу"},
     ]},
    {"id": "d02", "region": "Турция · Стамбул", "risk": True, "risk_words": ["протест", "митинг", "беспоряд", "акци"],
     "digest": [
         {"source": "ТАСС", "title": "В Стамбуле прошли массовые протесты, полиция перекрыла центр города", "risk": "беспорядки"},
         {"source": "The Guardian", "title": "Turkish lira slides to record low against dollar"},
         {"source": "ТАСС", "title": "Новый аэропорт Стамбула обслужил рекордное число пассажиров"},
     ]},
    {"id": "d03", "region": "ОАЭ · Дубай", "risk": False, "risk_words": [],
     "digest": [
         {"source": "The Guardian", "title": "Dubai unveils plans for world’s largest airport terminal"},
         {"source": "ТАСС", "title": "Турпоток из России в ОАЭ вырос на 20% за год"},
         {"source": "The Guardian", "title": "UAE announces new long-term tourist visa rules"},
     ]},
    {"id": "d04", "region": "Таиланд · Бангкок", "risk": True, "risk_words": ["наводн", "ливн", "затоп", "паводк"],
     "digest": [
         {"source": "The Guardian", "title": "Severe flooding hits Bangkok suburbs after record monsoon rains", "risk": "катаклизмы"},
         {"source": "ТАСС", "title": "Таиланд продлил безвизовый режим для россиян до 60 дней"},
     ]},
    {"id": "d05", "region": "Франция · Париж", "risk": True, "risk_words": ["забастов", "стачк"],
     "digest": [
         {"source": "ТАСС", "title": "Авиадиспетчеры Франции объявили забастовку, отменены сотни рейсов", "risk": "беспорядки"},
         {"source": "The Guardian", "title": "Paris museums extend opening hours for winter season"},
         {"source": "The Guardian", "title": "France to raise tourist tax in major cities next year"},
     ]},
    {"id": "d06", "region": "Армения · Ереван", "risk": False, "risk_words": [],
     "digest": [
         {"source": "ТАСС", "title": "Аэропорт Звартноц запустил новые рейсы в Россию"},
         {"source": "The Guardian", "title": "Armenia’s wine tourism booms as visitors return"},
     ]},
]
