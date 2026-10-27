"""news_engine — дайджест новостей региона назначения + risk-скоры.

Источники (BBC из этой сети недоступен — таймаут, поэтому):
- The Guardian: региональная лента /world/<slug>/rss (slug проверен на 200),
  при отсутствии slug — /world/rss + фильтр по ключевым словам;
- TASS: общий фид tass.ru/rss/v2.xml + фильтр по рус. ключевым словам.

Risk-классификация — keyword-словари (без LLM):
- «катаклизмы»: природные бедствия (earthquake, тайфун, наводнение, …);
- «беспорядки»: протесты/беспорядки/забастовки.

Кэш в SQLite (news_cache) с TTL NEWS_TTL_HOURS — вежливость к источникам.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path

from .. import db
from ..config import NEWS_MAX_AGE_DAYS, NEWS_MAX_ITEMS
from ..tools.net import http_get

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "news_regions.json"

GUARDIAN_BASE = "https://www.theguardian.com/world"
GUARDIAN_WORLD_RSS = f"{GUARDIAN_BASE}/rss"
TASS_RSS = "https://tass.ru/rss/v2.xml"

# Опасные события: keyword -> категория. Проверка по подстроке в нижнем регистре.
_RISK_KEYWORDS: dict[str, list[str]] = {
    "катаклизмы": [
        "earthquake", "tsunami", "typhoon", "hurricane", "cyclone",
        "flood", "wildfire", "volcano", "eruption", "landslide", "drought",
        "extreme heat", "snowstorm",
        "землетрясени", "цунами", "тайфун", "ураган", "наводнени",
        "пожар", "вулкан", "извержени", "оползень", "засух", "снегопад",
    ],
    "беспорядки": [
        "protest", "riot", "unrest", "clash", "curfew", "martial law",
        "on strike", "general strike", "airport strike", "rally against",
        "demonstration", "evacuat", "state of emergency",
        # голое «strike» не используем: ловит военные «strikes», а не забастовки
        "митинг", "протест", "беспорядк", "забастовк", "столкновени",
        "комендантск", "эвакуац", "чрезвычайное положение",
    ],
}

# Регулярки с кириллицей/латиницей для границ слов не нужны: проверяем подстроки.


@lru_cache(maxsize=1)
def _regions() -> dict:
    with open(_DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def classify_risk(title: str) -> str | None:
    """Категория опасного события по заголовку, None если обычная новость."""
    t = (title or "").lower()
    for category, words in _RISK_KEYWORDS.items():
        if any(w in t for w in words):
            return category
    return None


# Прилагательные от названий стран: «Turkish» не содержит «turkey» как подстроку,
# без этого заголовки вида «Turkish court …» выпадают из дайджеста.
_EN_ADJECTIVES = {
    "turkey": "turkish", "russia": "russian", "germany": "german",
    "china": "chinese", "france": "french", "spain": "spanish",
    "italy": "italian", "greece": "greek", "japan": "japanese",
    "egypt": "egyptian", "india": "indian", "brazil": "brazilian",
    "mexico": "mexican", "canada": "canadian", "australia": "australian",
    "ukraine": "ukrainian", "israel": "israeli", "morocco": "moroccan",
    "thailand": "thai", "vietnam": "vietnamese", "indonesia": "indonesian",
    "argentina": "argentine", "netherlands": "dutch", "sweden": "swedish",
    "norway": "norwegian", "denmark": "danish", "finland": "finnish",
    "poland": "polish", "croatia": "croatian", "serbia": "serbian",
    "belarus": "belarusian", "kazakhstan": "kazakh", "azerbaijan": "azerbaijani",
    "armenia": "armenian", "georgia": "georgian", "hungary": "hungarian",
    "romania": "romanian", "bulgaria": "bulgarian", "estonia": "estonian",
    "latvia": "latvian", "lithuania": "lithuanian", "philippines": "filipino",
    "malaysia": "malaysian", "cuba": "cuban", "chile": "chilean",
    "peru": "peruvian", "colombia": "colombian", "qatar": "qatari",
    "ethiopia": "ethiopian", "kenya": "kenyan",
}


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")


def _parse_rss(xml_text: str) -> list[dict]:
    """RSS 2.0 -> [{title, description, link, published(datetime|None)}]."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        desc = _strip_tags(it.findtext("description") or "")
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        published = None
        if pub:
            try:
                published = parsedate_to_datetime(pub)
            except (TypeError, ValueError):
                published = None
        if title and link:
            items.append(
                {"title": title, "description": desc, "link": link,
                 "published": published}
            )
    return items


def _matches(text: str, keywords: list[str]) -> bool:
    t = (text or "").lower()
    return any(k.lower() in t for k in keywords)


def _region_keywords_en(cfg: dict) -> list[str]:
    """EN-ключевые слова региона + прилагательные + названия городов."""
    kws = [cfg["en"].lower()] if cfg.get("en") else []
    kws += [k.lower() for k in cfg.get("keywords_en", [])]
    extra = [_EN_ADJECTIVES.get(k, None) for k in list(kws)]
    return kws + [a for a in extra if a]


def _collect_guardian(cfg: dict) -> list[dict]:
    """Айтемы Guardian: региональная лента (или /world/rss) + keyword-фильтр.

    Фильтр обязателен даже для региональной ленты: Guardian подмешивает
    мировые новости, и без фильтра в дайджест Японии попадают наводнения
    в Испании. Точность важнее полноты (ложные warning'ы хуже короткого дайджеста).
    Матчим по заголовку ИЛИ description (в description чаще встречается город).
    """
    slug = cfg.get("guardian_slug")
    url = f"{GUARDIAN_BASE}/{slug}/rss" if slug else GUARDIAN_WORLD_RSS
    keywords = _region_keywords_en(cfg)
    try:
        items = _parse_rss(http_get(url))
    except RuntimeError:
        return []
    out = []
    for it in items:
        in_title = _matches(it["title"], keywords)
        if not in_title and not _matches(it["description"], keywords):
            continue
        it["source"] = "The Guardian"
        it["matched_title"] = in_title
        out.append(it)
    return out


def _collect_tass(cfg: dict, country_ru: str, city_ru: str | None) -> list[dict]:
    """Айтемы TASS: общий фид + фильтр по рус. ключевым словам (страна + город)."""
    kws = list(cfg.get("keywords_ru", [])) + [country_ru]
    if city_ru:
        kws.append(city_ru)
    try:
        items = _parse_rss(http_get(TASS_RSS))
    except RuntimeError:
        return []
    out = []
    for it in items:
        in_title = _matches(it["title"], kws)
        if not in_title and not _matches(it["description"], kws):
            continue
        it["source"] = "ТАСС"
        it["matched_title"] = in_title
        out.append(it)
    return out


def get_news(
    country_ru: str,
    city_ru: str | None = None,
    max_items: int = NEWS_MAX_ITEMS,
    use_cache: bool = True,
) -> dict:
    """Дайджест последних новостей региона + warnings об опасных событиях.

    Возвращает {
      region, digest: [{title,url,published,source,risk}], warnings: [...],
      risk_level: "ok"|"warning", fetched_at
    }. При недоступности источников — digest пуст, но без исключений.
    """
    region_key = f"{country_ru}|{city_ru or ''}"
    db.init_db()  # свежий процесс может прийти до первого вызова price_engine
    if use_cache:
        cached = db.get_news_cache(region_key)
        if cached:
            return cached

    cfg = _regions().get(country_ru, {})
    if not cfg:
        # Неизвестный регион — не фильтруем Guardian по стране, честно помечаем.
        cfg = {"en": country_ru, "guardian_slug": None, "keywords_en": [],
               "keywords_ru": []}

    cutoff = datetime.now(timezone.utc) - timedelta(days=NEWS_MAX_AGE_DAYS)
    raw = _collect_guardian(cfg) + _collect_tass(cfg, country_ru, city_ru)

    seen_titles: set[str] = set()
    digest: list[dict] = []
    for it in raw:
        norm = re.sub(r"\s+", " ", it["title"]).strip().lower()
        if norm in seen_titles:
            continue
        seen_titles.add(norm)
        if it["published"] and it["published"] < cutoff:
            continue
        digest.append(
            {
                "title": it["title"],
                "url": it["link"],
                "published": it["published"].isoformat() if it["published"] else None,
                "source": it["source"],
                "matched_title": it.get("matched_title", True),
                "risk": classify_risk(it["title"]),
            }
        )

    # Заголовочные матчи первыми (точнее), внутри каждой группы — по свежести.
    by_pub = lambda d: d["published"] or ""
    title_first = sorted([d for d in digest if d["matched_title"]], key=by_pub, reverse=True)
    desc_only = sorted([d for d in digest if not d["matched_title"]], key=by_pub, reverse=True)
    digest = (title_first + desc_only)[:max_items]
    for d in digest:
        d.pop("matched_title", None)

    warnings = [d for d in digest if d["risk"]]
    payload = {
        "region": country_ru,
        "digest": digest,
        "warnings": warnings,
        "risk_level": "warning" if warnings else "ok",
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
    if use_cache:
        db.set_news_cache(region_key, payload)
    return payload
