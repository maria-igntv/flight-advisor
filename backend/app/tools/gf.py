"""Лёгкий скрейпер Google Flights.

fast_flights как готовую библиотеку использовать нельзя (её HTTP-клиент primp
в этой среде не работает, а парсер местами падает). Поэтому:
- URL (protobuf-параметры `tfs`) строим через fast_flights.create_query — работает;
- страницу качаем через httpx, при неудаче — curl (в этой среде curl надёжнее);
- цены/авиакомпании парсим сами из встроенного JSON `AF_initDataCallback`.

Зависимости: httpx, selectolax, fast-flights, certifi.
"""
from __future__ import annotations

import json
import subprocess
from urllib.parse import urlencode

import httpx
from selectolax.lexbor import LexborHTMLParser

from fast_flights import FlightQuery, Passengers, create_query

from .. import settings

GOOGLE_FLIGHTS_URL = "https://www.google.com/travel/flights"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)
# Серверы Streamlit Cloud стоят в США/ЕС: без этих cookies Google отдаёт им
# страницу согласия (consent.google.com) вместо выдачи — цен в ней нет.
COOKIES = {
    "CONSENT": "PENDING+987",
    "SOCS": "CAESHAgBEhJnd3NfMjAyMzA4MTAtMF9SQzIaAmRlIAEaBgiAo_CmBg",
}
HEADERS = {"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"}

# Диагностика последнего запроса (страница «Диагностика» в UI, /api/diag).
LAST_FETCH: dict = {}


def _parse_payload(html: str) -> dict:
    """Достаём и парсим JSON из script.ds:1 (AF_initDataCallback)."""
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        return {}
    data = script.text().split("data:", 1)[1].rsplit(",", 1)[0]
    return json.loads(data)


def _hm(v: list | None) -> str | None:
    """[час, минута] -> 'HH:MM'."""
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return f"{v[0]:02d}:{v[1]:02d}"
    return None


def _ymd(v: list | None) -> str | None:
    """[год, месяц, день] -> 'YYYY-MM-DD'."""
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        return f"{v[0]:04d}-{v[1]:02d}-{v[2]:02d}"
    return None


def parse_flights(html: str) -> list[dict]:
    """Извлекаем список офферов с деталями рейса.

    Структура payload (проверена 2026-10-24):
    оффер k: k[1][0][1]=цена; k[0]=детали:
      [3]=IATA откуда, [4]=дата вылета, [5]=время вылета,
      [6]=IATA куда, [7]=дата прилёта, [8]=время прилёта, [9]=длительность (мин)
    сегмент sf (элементы k[0][2]):
      [3]/[6]=IATA from/to, [8]=время вылета, [11]=длительность (мин),
      [17]=самолёт, [22]=[код, номер рейса, None, авиакомпания]
    """
    try:
        payload = _parse_payload(html)
        raw = payload[3][0]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return []
    if not raw:
        return []

    flights: list[dict] = []
    for k in raw:
        try:
            # Цена бывает пустой (проверено на MOW->BAX: Google находит рейсы,
            # но перевозчик не отдаёт цены в источник) — оффер сохраняем с price=None.
            try:
                price = k[1][0][1]
            except (IndexError, TypeError):
                price = None
            flight = k[0]
            airlines = flight[1]
            segments = []
            for sf in flight[2]:
                fn = sf[22] if len(sf) > 22 else None
                segments.append(
                    {
                        "from": sf[3],
                        "to": sf[6],
                        "dep_date": _ymd(sf[20]),
                        "dep_time": _hm(sf[8]),
                        "duration_min": sf[11] if len(sf) > 11 else None,
                        "aircraft": sf[17] if len(sf) > 17 else None,
                        "flight_no": f"{fn[0]} {fn[1]}" if fn and fn[0] and fn[1] else None,
                        "airline": fn[3] if fn and len(fn) > 3 else None,
                    }
                )
            dep_time = _hm(flight[5]) if len(flight) > 5 else None
            arr_time = _hm(flight[8]) if len(flight) > 8 else None
            flights.append(
                {
                    "price": price,
                    "airlines": airlines,
                    "from": flight[3] if len(flight) > 3 else None,
                    "to": flight[6] if len(flight) > 6 else None,
                    "dep_date": _ymd(flight[4]) if len(flight) > 4 else None,
                    "dep_time": dep_time,
                    "arr_date": _ymd(flight[7]) if len(flight) > 7 else None,
                    "arr_time": arr_time,
                    "duration_min": flight[9] if len(flight) > 9 else None,
                    "stops": max(len(segments) - 1, 0),
                    "segments": segments,
                }
            )
        except (IndexError, TypeError, AttributeError):
            continue
    return flights


def page_kind(html: str, final_url: str = "") -> str:
    """Что вернул Google: flights / consent / captcha / empty / unknown."""
    if not html:
        return "empty"
    if "consent.google" in final_url or "consent.google.com" in html[:5000]:
        return "consent"
    if "/sorry/" in final_url or "unusual traffic" in html or "recaptcha" in html[:20000]:
        return "captcha"
    if "AF_initDataCallback" in html and "ds:1" in html:
        return "flights"
    return "unknown"


def _proxy() -> str | None:
    """GF_PROXY (env или Secrets) — например, http://user:pass@host:port.
    Нужен, если Google блокирует IP облака капчей."""
    return settings.get("GF_PROXY") or None


def _curl(url: str) -> tuple[str, str]:
    cookie = "; ".join(f"{k}={v}" for k, v in COOKIES.items())
    proxy = _proxy()
    proc = subprocess.run(
        ["curl", "-sL", "--max-time", "30", "-A", UA, "-b", cookie,
         "-H", f"Accept-Language: {HEADERS['Accept-Language']}",
         *(["-x", proxy] if proxy else []),
         "-w", "\n__FINAL_URL__%{url_effective}", url],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"fetch failed (curl rc={proc.returncode})")
    body, _, final = proc.stdout.rpartition("\n__FINAL_URL__")
    return body, final


def _fetch_html(params: dict) -> str:
    """Качаем страницу: httpx, иначе (или если пришла не выдача) — curl."""
    url = f"{GOOGLE_FLIGHTS_URL}?{urlencode(params)}"
    html, final, via, err = "", "", "httpx", None
    try:
        r = httpx.get(
            GOOGLE_FLIGHTS_URL,
            params=params,
            headers=HEADERS,
            cookies=COOKIES,
            timeout=20,
            follow_redirects=True,
            proxy=_proxy(),
        )
        r.raise_for_status()
        html, final = r.text, str(r.url)
    except Exception as e:  # noqa: BLE001 — сеть флаки, пробуем curl
        err = f"httpx: {type(e).__name__}"
    if page_kind(html, final) != "flights":
        try:
            html, final = _curl(url)
            via = "curl"
        except (RuntimeError, OSError) as e:
            err = f"{err or ''} curl: {e}".strip()
            if not html:
                LAST_FETCH.update(url=url, via="none", kind="empty", length=0, error=err)
                raise RuntimeError(err) from e
    LAST_FETCH.update(url=url, final_url=final, via=via, kind=page_kind(html, final),
                      length=len(html), error=err, proxy=bool(_proxy()))
    return html


def _pax(adults: int, children: int, infants: int) -> Passengers:
    """Младенцы — «на руках» (без места), как по умолчанию у авиакомпаний."""
    return Passengers(adults=adults, children=children, infants_on_lap=infants)


def _build_query(
    origin: str,
    dest: str,
    date: str,
    return_date: str | None,
    currency: str,
    passengers: Passengers | None,
):
    flights_query = [FlightQuery(date=date, from_airport=origin, to_airport=dest)]
    if return_date:
        flights_query.append(FlightQuery(date=return_date, from_airport=dest, to_airport=origin))
    return create_query(
        flights=flights_query,
        trip="round-trip" if return_date else "one-way",
        currency=currency,
        language="ru",
        passengers=passengers,
    )


def build_search_url(
    origin: str,
    dest: str,
    date: str,
    *,
    return_date: str | None = None,
    currency: str = "RUB",
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
) -> str:
    """Готовый URL Google Flights для кнопки-ссылки (тот же поиск, из которого данные)."""
    return _build_query(
        origin, dest, date, return_date, currency,
        _pax(adults, children, infants),
    ).url()


def search_flights(
    origin: str,
    dest: str,
    date: str,
    *,
    return_date: str | None = None,
    currency: str = "RUB",
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
) -> list[dict]:
    """Поиск рейсов: возвращает список офферов с ценами (за всех пассажиров)."""
    q = _build_query(
        origin, dest, date, return_date, currency,
        _pax(adults, children, infants),
    )
    return parse_flights(_fetch_html(q.params()))
