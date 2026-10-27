"""FastAPI entry — Flight Ticket Advisor.

Те же движки, что у Streamlit UI (engines.advisor.analyze): цены, вердикт,
прогноз, новости, виза, календарь, AI-помощник.

Запуск:  uvicorn backend.app.main:app --port 8000  → документация на /docs
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

from . import db
from .config import CURRENCY
from .engines import calendar_engine, chat_engine
from .engines.advisor import analyze, forecast_for
from .engines.news_engine import get_news
from .engines.price_engine import get_price_calendar
from .engines.visa_engine import check_trip
from .tools import gf
from .tools.airports import region_for

db.init_db()

app = FastAPI(title="Flight Ticket Advisor", version="0.2.0")

Currency = Literal["RUB", "USD", "EUR"]


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "llm_provider": chat_engine.provider()}


class SearchRequest(BaseModel):
    origin: str = Field(examples=["MOW"])
    dest: str = Field(examples=["TYO"])
    dep_start: date
    dep_end: date | None = Field(None, description="конец диапазона вылетов; пусто — одна дата")
    return_date: date | None = Field(None, description="дата возвращения для туда-обратно")
    adults: int = Field(1, ge=1, le=9)
    children: int = Field(0, ge=0, le=8)
    infants: int = Field(0, ge=0, le=4)
    currency: Currency = CURRENCY
    with_news: bool = True
    with_forecast: bool = True

    @model_validator(mode="after")
    def _check(self) -> "SearchRequest":
        self.origin = self.origin.strip().upper()
        self.dest = self.dest.strip().upper()
        if self.origin == self.dest:
            raise ValueError("origin и dest совпадают")
        if self.dep_start < date.today():
            raise ValueError("dep_start в прошлом")
        if self.dep_end and self.dep_end < self.dep_start:
            raise ValueError("dep_end раньше dep_start")
        if self.dep_end and (self.dep_end - self.dep_start).days > 30:
            raise ValueError("диапазон вылетов не шире 31 дня")
        if self.return_date and self.return_date <= (self.dep_end or self.dep_start):
            raise ValueError("return_date должна быть позже вылета")
        return self


def _public(res: dict) -> dict:
    """Ключи с «_» в UI служебные — в API отдаём их без префикса."""
    return {k.lstrip("_"): v for k, v in res.items()}


@app.post("/api/recommend")
def api_recommend(req: SearchRequest) -> dict:
    """Полный отчёт: календарь цен, самая дешёвая дата, вердикт BUY/WAIT/WATCH
    с сигналами, динамика, прогноз до вылета, новости, виза, занятость."""
    res = analyze(
        req.origin, req.dest, req.dep_start, req.dep_end, req.currency,
        return_date=req.return_date, adults=req.adults, children=req.children,
        infants=req.infants, with_news=req.with_news,
    )
    if req.with_forecast:
        res["forecast"] = forecast_for(res)
    if not res.get("cheapest_date"):
        res["source_diagnostics"] = {k: v for k, v in gf.LAST_FETCH.items() if k != "url"}
    return _public(res)


@app.get("/api/calendar")
def api_calendar(
    origin: str, dest: str, start: date, end: date | None = None,
    return_date: date | None = None, adults: int = 1, children: int = 0,
    infants: int = 0, currency: Currency = CURRENCY,
) -> dict:
    """Только календарь min-цен по датам вылета (без вердикта)."""
    o, d = origin.strip().upper(), dest.strip().upper()
    cal = get_price_calendar(
        o, d, start, end or start, currency, return_date=return_date,
        adults=adults, children=children, infants=infants,
    )
    return {"origin": o, "dest": d, "currency": currency, "calendar": cal}


@app.get("/api/news/{dest}")
def api_news(dest: str) -> dict:
    city, country = region_for(dest.strip().upper())
    if not country:
        raise HTTPException(404, "регион для этого IATA-кода неизвестен")
    return get_news(country, city)


@app.get("/api/visa/{dest}")
def api_visa(dest: str, dep: date, ret: date | None = None) -> dict:
    _, country = region_for(dest.strip().upper())
    return {"country": country, "visa": check_trip(country, dep, ret)}


@app.get("/api/busy")
def api_busy(start: date, end: date) -> dict:
    """Занятые дни из снапшота календаря VK Workspace."""
    if not calendar_engine.has_data():
        return {"has_data": False, "busy": {}}
    return {"has_data": True, "busy": calendar_engine.busy_dates(start.isoformat(), end.isoformat())}


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    question: str
    history: list[ChatMessage] = []


@app.post("/api/chat")
def api_chat(req: ChatRequest) -> dict:
    """AI-помощник (Anthropic локально / YandexGPT в облаке) с tool-use поиском цен."""
    err = chat_engine.configuration_error()
    if err:
        raise HTTPException(503, err)
    try:
        answer = chat_engine.ask(req.question, [m.model_dump() for m in req.history])
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from e
    return {"provider": chat_engine.provider(), "answer": answer}


@app.get("/api/diag")
def api_diag() -> dict:
    """Что вернул Google Flights при последнем запросе (consent/captcha/flights)."""
    return {"last_fetch": {k: v for k, v in gf.LAST_FETCH.items() if k != "url"},
            "llm_provider": chat_engine.provider()}
