"""MCP-сервер VK WorkSpace Calendar поверх официально поддерживаемого CalDAV."""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import caldav
from mcp.server import MCPServer
from pydantic import BaseModel


class CalendarEvent(BaseModel):
    date: str
    time: str
    title: str
    start: str
    end: str


class CalendarEventsResponse(BaseModel):
    events: list[CalendarEvent]
    fetched_at: str


mcp = MCPServer("VK WorkSpace Calendar", version="1.0.0")


def _local_datetime(value, timezone: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone) if value.tzinfo is None else value.astimezone(timezone)
    return datetime.combine(value, time.min, tzinfo=timezone)


def _event_days(start: datetime, end: datetime, all_day: bool) -> list[date]:
    if end <= start:
        return [start.date()]
    last = end - timedelta(days=1) if all_day else end - timedelta(microseconds=1)
    return [start.date() + timedelta(days=i) for i in range((last.date() - start.date()).days + 1)]


def _component_events(component, timezone: ZoneInfo) -> list[dict]:
    if str(component.get("STATUS", "")).upper() == "CANCELLED":
        return []
    if str(component.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT":
        return []

    start_prop = component.get("DTSTART")
    if start_prop is None:
        return []
    all_day = not isinstance(start_prop.dt, datetime)
    start = _local_datetime(start_prop.dt, timezone)
    end_prop = component.get("DTEND")
    end = _local_datetime(end_prop.dt, timezone) if end_prop is not None else start + timedelta(hours=1)
    title = str(component.get("SUMMARY", "занято")) or "занято"
    time_range = "весь день" if all_day else f"{start:%H:%M}–{end:%H:%M}"

    return [
        {
            "date": day.isoformat(),
            "time": time_range,
            "title": title,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        for day in _event_days(start, end, all_day)
    ]


def fetch_events(start: date, end: date) -> list[dict]:
    url = os.environ.get("VK_CALDAV_URL", "").strip()
    username = os.environ.get("VK_CALDAV_USERNAME", "").strip()
    password = os.environ.get("VK_CALDAV_PASSWORD", "").strip()
    if not all((url, username, password)):
        raise ValueError("Не настроены VK_CALDAV_URL, VK_CALDAV_USERNAME и VK_CALDAV_PASSWORD")

    timezone = ZoneInfo(os.environ.get("VK_CALENDAR_TIMEZONE", "Europe/Moscow"))
    with caldav.DAVClient(url=url, username=username, password=password, timeout=15) as client:
        calendar = client.calendar(url=url)
        found = calendar.search(
            start=start,
            end=end + timedelta(days=1),
            event=True,
            expand=True,
        )

    events: list[dict] = []
    for event in found:
        calendar_data = event.get_icalendar_instance()
        for component in calendar_data.subcomponents:
            if component.name == "VEVENT":
                events.extend(_component_events(component, timezone))
    in_range = [event for event in events if start.isoformat() <= event["date"] <= end.isoformat()]
    return sorted(in_range, key=lambda event: (event["date"], event["start"], event["title"]))


@mcp.tool(
    name="calendar_list_events",
    title="События VK WorkSpace Calendar",
    description="Возвращает события календаря за включительный диапазон дат, включая повторяющиеся.",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
def calendar_list_events(start: str, end: str) -> CalendarEventsResponse:
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError("Даты должны быть в формате YYYY-MM-DD") from exc
    if end_date < start_date:
        raise ValueError("end не может быть раньше start")
    if (end_date - start_date).days > 366:
        raise ValueError("Диапазон не может превышать 367 дней")
    return CalendarEventsResponse(
        events=fetch_events(start_date, end_date),
        fetched_at=datetime.now().isoformat(timespec="seconds"),
    )


if __name__ == "__main__":
    mcp.run()
