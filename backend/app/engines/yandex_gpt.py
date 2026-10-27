"""YandexGPT для веб-версии (Streamlit Cloud).

Локальный помощник ходит в Anthropic-гейтвей. На streamlit.app этих
переменных нет, поэтому облако вызывает Yandex Cloud Foundation Models.

Секреты (окружение или Secrets Streamlit, плоские строковые ключи):
  YANDEX_GPT_API_KEY     — API-ключ сервисного аккаунта
  YANDEX_GPT_FOLDER_ID   — идентификатор каталога
  YANDEX_GPT_MODEL       — необязательно, по умолчанию yandexgpt/latest
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import httpx

API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
_DEFAULT_MODEL = "yandexgpt/latest"


@dataclass
class Turn:
    text: str
    tool_calls: list[dict]


def _setting(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        import streamlit as st

        if name in st.secrets:
            raw = st.secrets[name]
            return str(raw).strip() if raw is not None else ""
    except Exception:
        return ""
    return ""


def ready() -> bool:
    return bool(_setting("YANDEX_GPT_API_KEY") and _setting("YANDEX_GPT_FOLDER_ID"))


def _model_uri(folder_id: str, model: str) -> str:
    model = model.strip()
    if model.startswith("gpt://"):
        return model
    return f"gpt://{folder_id}/{model.lstrip('/')}"


def _post(url: str, api_key: str, payload: dict) -> httpx.Response:
    return httpx.post(
        url,
        headers={
            "Authorization": f"Api-Key {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=90,
    )


def _error_text(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        return (resp.text or "")[:400]
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:500]
        if isinstance(err, str):
            return err[:500]
    return (resp.text or "")[:400]


def _parse_arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_turn(data: dict) -> Turn:
    if not isinstance(data, dict):
        raise RuntimeError("YandexGPT вернул пустой ответ.")
    if "error" in data and "result" not in data:
        err = data["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise RuntimeError(f"YandexGPT: {message}")
    try:
        message = data["result"]["alternatives"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError("YandexGPT вернул ответ без текста.") from e
    text = str(message.get("text") or "").strip()
    calls = []
    tool_list = message.get("toolCallList") or {}
    for call in tool_list.get("toolCalls") or []:
        fn = call.get("functionCall") or call
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        calls.append({"name": name, "arguments": _parse_arguments(fn.get("arguments"))})
    return Turn(text=text, tool_calls=calls)


def complete(
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    max_tokens: int = 2048,
) -> Turn:
    """Один синхронный запрос completion. Бросает RuntimeError без ключа в тексте."""
    api_key = _setting("YANDEX_GPT_API_KEY")
    folder_id = _setting("YANDEX_GPT_FOLDER_ID")
    if not api_key or not folder_id:
        raise RuntimeError("YandexGPT: не заданы YANDEX_GPT_API_KEY и YANDEX_GPT_FOLDER_ID.")
    model = _setting("YANDEX_GPT_MODEL") or _DEFAULT_MODEL
    payload: dict = {
        "modelUri": _model_uri(folder_id, model),
        "completionOptions": {
            "stream": False,
            "temperature": 0.3,
            "maxTokens": max_tokens,
        },
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
    url = _setting("YANDEX_GPT_API_URL") or API_URL
    try:
        resp = _post(url, api_key, payload)
    except httpx.HTTPError as e:
        raise RuntimeError(f"YandexGPT недоступен: {e.__class__.__name__}") from e
    if resp.status_code >= 400:
        # Модель без function calling — повторяем обычным текстом, чат остаётся живым.
        if tools and resp.status_code in (400, 422):
            return complete(messages, tools=None, max_tokens=max_tokens)
        raise RuntimeError(f"YandexGPT ответил {resp.status_code}: {_error_text(resp)}")
    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        raise RuntimeError("YandexGPT вернул не-JSON.") from e
    return _parse_turn(data)


def complete_text(system: str, user: str, max_tokens: int = 400) -> str:
    turn = complete(
        [
            {"role": "system", "text": system},
            {"role": "user", "text": user},
        ],
        tools=None,
        max_tokens=max_tokens,
    )
    return turn.text
