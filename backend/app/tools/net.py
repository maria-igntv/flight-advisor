"""Простой HTTP-GET: httpx, при неудаче — curl subprocess.

Паттерн проверен в этой среде (см. gf._fetch_html): httpx периодически
таймаутит на SSL-handshake, curl стабилен. Используется news_engine'ом.
"""
from __future__ import annotations

import subprocess

import httpx

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)


def http_get(url: str, timeout: int = 20) -> str:
    """Тело ответа как текст. Бросает RuntimeError, если оба способа упали."""
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
        return r.text
    except Exception:
        proc = subprocess.run(
            ["curl", "-sSL", "--max-time", str(timeout + 10), "-A", UA, url],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0 or not proc.stdout:
            raise RuntimeError(f"http_get failed: {url} (curl rc={proc.returncode})")
        return proc.stdout
