"""MCP-клиент календаря: удалённый Streamable HTTP или локальный stdio-сервер."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

from ..settings import get

_ROOT = Path(__file__).resolve().parents[3]


def remote_url() -> str:
    return get("CALENDAR_MCP_URL")


def configured() -> bool:
    return bool(remote_url()) or all(
        get(name) for name in ("VK_CALDAV_URL", "VK_CALDAV_USERNAME", "VK_CALDAV_PASSWORD")
    )


def missing_settings() -> list[str]:
    if remote_url():
        return []
    return [
        name
        for name in ("VK_CALDAV_URL", "VK_CALDAV_USERNAME", "VK_CALDAV_PASSWORD")
        if not get(name)
    ]


async def _list_events(start: str, end: str) -> dict:
    if remote_url():
        transport = remote_url()
    else:
        transport = StdioServerParameters(
            command=sys.executable,
            args=["-m", "backend.app.mcp.vk_calendar_server"],
            cwd=_ROOT,
            env={
                name: get(name)
                for name in (
                    "VK_CALDAV_URL",
                    "VK_CALDAV_USERNAME",
                    "VK_CALDAV_PASSWORD",
                    "VK_CALENDAR_TIMEZONE",
                )
                if get(name)
            },
        )
    async with Client(transport) as client:
        result = await client.call_tool(
            "calendar_list_events", {"start": start, "end": end}, read_timeout_seconds=25
        )
    if result.is_error:
        raise RuntimeError("MCP-сервер календаря вернул ошибку")
    if not result.structured_content or not isinstance(result.structured_content.get("events"), list):
        raise RuntimeError("MCP-сервер календаря вернул некорректный ответ")
    return result.structured_content


def list_events(start: str, end: str) -> dict:
    return asyncio.run(_list_events(start, end))
