"""Секреты и настройки: переменные окружения, затем Secrets Streamlit Cloud."""
from __future__ import annotations

import os


def get(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        import streamlit as st

        if name in st.secrets:
            raw = st.secrets[name]
            return str(raw).strip() if raw is not None else ""
    except Exception:  # noqa: BLE001 — нет streamlit/secrets.toml: настройки нет
        return ""
    return ""
