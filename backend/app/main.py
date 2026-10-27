"""FastAPI entry — Flight Ticket Advisor."""
from __future__ import annotations

from datetime import date

from fastapi import FastAPI
from pydantic import BaseModel

from . import db
from .config import CURRENCY
from .engines.price_engine import get_price_calendar, recommend

db.init_db()

app = FastAPI(title="Flight Ticket Advisor", version="0.1.0")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


class RecommendRequest(BaseModel):
    origin: str
    dest: str
    dep_start: date
    dep_end: date
    currency: str = CURRENCY


@app.post("/api/recommend")
def api_recommend(req: RecommendRequest) -> dict:
    return recommend(
        req.origin.strip().upper(),
        req.dest.strip().upper(),
        req.dep_start,
        req.dep_end,
        req.currency,
    )


@app.get("/api/calendar")
def api_calendar(
    origin: str, dest: str, start: date, end: date, currency: str = CURRENCY
) -> dict:
    cal = get_price_calendar(
        origin.strip().upper(), dest.strip().upper(), start, end, currency
    )
    return {"origin": origin.strip().upper(), "dest": dest.strip().upper(), "calendar": cal}
