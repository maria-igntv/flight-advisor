"""Flight Ticket Advisor — Streamlit UI.

Запуск из корня проекта:  streamlit run frontend/app.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import altair as alt
import pandas as pd
import streamlit as st

from backend.app import db
from backend.app.engines import calendar_engine, chat_engine
from backend.app.engines.alerts_engine import dynamics
from backend.app.engines.demo_history import ensure_demo_history
from backend.app.engines.forecast_engine import forecast_curve, forecast_summary
from backend.app.engines.news_engine import get_news
from backend.app.engines.orchestrator import verdict as make_verdict
from backend.app.engines.price_engine import recommend
from backend.app.tools.airports import OTHER, load_entries, region_for
from backend.app.tools.gf import build_search_url

st.set_page_config(page_title="Flight Ticket Advisor", page_icon="✈️", layout="wide")

# ---------------------------------------------------------------- палитры/тема
# Тема через переопределение CSS-ПЕРЕМЕННЫХ Streamlit: виджеты (selectbox,
# date_input, их всплывающие панели) красятся из этих переменных, а не из
# цвета .stApp. Порталы (popover/menu/календарь) живут ВНЕ .stApp — их красим
# отдельно на уровне документа. Это чинит «не перекрашиваются записи».

_PALETTES = {
    "light": {
        "text": "#1f2328",
        "bg": "#ffffff",
        "secondary": "#f6f8fa",
        "primary": "#2563eb",
        "muted": "#57606a",
        "hero_from": "#2563eb",
        "hero_to": "#7c3aed",
        "hero_text": "#ffffff",
        "card_border": "#d0d7de",
    },
    "dark": {
        "text": "#fafafa",
        "bg": "#0e1117",
        "secondary": "#161b22",
        "primary": "#60a5fa",
        "muted": "#8b949e",
        "hero_from": "#1d4ed8",
        "hero_to": "#6d28d9",
        "hero_text": "#ffffff",
        "card_border": "#30363d",
    },
}


def _theme_css(p: dict) -> str:
    return f"""
<style>
/* Переменные темы — на app, сайдбар и порталы (всплывашки живут вне .stApp). */
:root, .stApp, [data-testid="stSidebar"],
[data-baseweb="popover"], [data-baseweb="menu"], [data-baseweb="modal"],
[data-baseweb="calendar"], [data-baseweb="datepicker"], [data-baseweb="tooltip"] {{
    --text-color: {p['text']};
    --background-color: {p['bg']};
    --secondary-background-color: {p['secondary']};
    --primary-color: {p['primary']};
    --secondary-text-color: {p['muted']};
}}
.stApp, [data-testid="stSidebar"] {{
    background-color: {p['bg']};
    color: {p['text']};
}}
[data-testid="stSidebar"] {{ background-color: {p['secondary']}; }}
[data-testid="stHeader"] {{ background-color: {p['bg']}; }}
/* Порталы: явные фон/текст — переменные наследуются от body, не от .stApp. */
[data-baseweb="popover"], [data-baseweb="menu"], [data-baseweb="calendar"],
[data-baseweb="datepicker"] {{
    background-color: {p['secondary']};
    color: {p['text']};
}}
[data-baseweb="popover"] *, [data-baseweb="menu"] * {{ color: {p['text']}; }}
/* ЯВНЫЕ цвета текста с !important: часть виджетов (метрики, лейблы форм,
   toggle, date_input) получает литеральные цвета из базовой темы Streamlit —
   переменные их не перебивают. Красим напрямую, чтобы тема применялась всегда. */
[data-testid="stWidgetLabel"] p,
[data-testid="stMetricLabel"],
[data-testid="stMetricValue"],
[data-testid="stMetricDelta"],
[data-testid="stToggle"] [role="switch"] *,
[data-testid="stDateInput"] input,
[data-testid="stDateInput"] [class*="DateInput"],
[data-testid="stDateInput"] [class*="placeholder"],
[data-baseweb="select"] > div > div,
[data-baseweb="input"] input,
[data-testid="stForm"] label,
.stRadio label p,
[data-testid="stExpander"] summary,
[data-testid="stExpander"] summary p,
p.caption {{ color: {p['text']} !important; -webkit-text-fill-color: {p['text']} !important; }}
[data-testid="stCaptionWrapper"], [data-testid="stCaptionContainer"],
[data-testid="stMarkdownContainer"] p.caption,
.small-text {{ color: {p['muted']} !important; -webkit-text-fill-color: {p['muted']} !important; }}
/* Значения selectbox в сайдбаре и форме. */
[data-baseweb="select"] > div > div {{ color: {p['text']} !important; }}
[data-baseweb="select"] svg {{ fill: {p['text']} !important; }}
/* Поля ввода и значения selectbox. */
[data-baseweb="input"] > div {{
    background-color: {p['secondary']};
    border-color: {p['card_border']};
}}
[data-baseweb="input"] input {{ color: {p['text']}; }}
[data-baseweb="select"] > div > div {{ background-color: {p['secondary']}; color: {p['text']}; }}
[data-baseweb="select"] svg {{ fill: {p['text']}; }}
/* Табы, экспандеры, чат. */
.stTabs [data-baseweb="tab"] {{ color: {p['muted']}; }}
.stTabs [aria-selected="true"] {{ color: {p['text']}; }}
div[data-testid="stExpander"] {{
    background-color: {p['secondary']};
    border-color: {p['card_border']};
    border-radius: 10px;
}}
/* Диалоговое окно AI-помощника: тёмное в тёмной теме, с явной рамкой. */
[data-testid="stChatMessage"] {{
    background-color: {p['secondary']} !important;
    border: 1px solid {p['card_border']};
    border-radius: 12px;
    padding: 10px 14px;
}}
[data-testid="stChatMessage"] p {{ color: {p['text']}; }}
/* Поле ввода чата — тоже в тему. */
[data-testid="stChatInput"] form,
[data-testid="stChatInput"] textarea {{
    background-color: {p['secondary']} !important;
    color: {p['text']} !important;
    border-color: {p['card_border']};
    border-radius: 12px;
}}
/* Нижняя панель, в которой живёт поле ввода чата: по умолчанию она белая
   и в тёмной теме выглядит чужеродным прямоугольником внизу страницы. */
[data-testid="stBottom"], [data-testid="stBottom"] > div,
[data-testid="stChatInput"] {{
    background-color: {p['bg']} !important;
}}
/* Хиро-баннер. */
.hero {{
    background: linear-gradient(120deg, {p['hero_from']}, {p['hero_to']});
    color: {p['hero_text']};
    border-radius: 16px;
    padding: 28px 32px;
    margin-bottom: 20px;
}}
.hero h1 {{ color: {p['hero_text']}; margin: 0 0 6px 0; font-size: 34px; }}
.hero p {{ color: {p['hero_text']}; opacity: 0.9; margin: 0; font-size: 16px; }}
/* Карточка лучшего рейса. */
.flight-card {{
    background-color: {p['secondary']};
    border: 1px solid {p['card_border']};
    border-left: 4px solid {p['primary']};
    border-radius: 12px;
    padding: 18px 22px;
    margin-bottom: 12px;
}}
.flight-card .price {{ font-size: 30px; font-weight: 700; color: {p['primary']}; }}
.flight-card .meta {{ color: {p['muted']}; font-size: 14px; }}
.flight-card .route {{ font-size: 17px; font-weight: 600; color: {p['text']}; }}
/* Карточка новости. */
.news-item {{
    padding: 8px 12px;
    border-left: 3px solid {p['card_border']};
    margin-bottom: 6px;
    font-size: 14px;
}}
.news-item .src {{ color: {p['muted']}; font-size: 12px; }}
/* Бегущая строка критических новостей (⚠️): гориз. скролл, пауза при наведении. */
.news-marquee {{
    border: 1px solid #ef4444;
    border-radius: 10px;
    background-color: rgba(239, 68, 68, 0.10);
    overflow: hidden;
    white-space: nowrap;
    padding: 9px 0;
    margin: 6px 0 14px 0;
    font-size: 14px;
}}
.news-marquee .track {{
    display: inline-block;
    padding-left: 100%;
    animation: news-scroll 45s linear infinite;
}}
.news-marquee:hover .track {{ animation-play-state: paused; }}
.news-marquee a {{ color: {p['primary']}; text-decoration: none; font-weight: 600; }}
@keyframes news-scroll {{
    0% {{ transform: translateX(0); }}
    100% {{ transform: translateX(-100%); }}
}}
/* Итоговый вердикт orchestrator'а. */
.verdict {{
    border-radius: 12px;
    padding: 16px 22px;
    margin-bottom: 14px;
    font-size: 16px;
}}
.verdict .v-title {{ font-size: 20px; font-weight: 700; margin-bottom: 4px; }}
.verdict .v-text {{ font-size: 14px; }}
.verdict .v-conf {{ font-size: 12px; opacity: 0.75; }}
.verdict-BUY {{ background: rgba(34, 197, 94, 0.13); border: 1px solid #22c55e; color: {p['text']}; }}
.verdict-BUY .v-title {{ color: #16a34a; }}
.verdict-WAIT {{ background: rgba(245, 158, 11, 0.13); border: 1px solid #f59e0b; color: {p['text']}; }}
.verdict-WAIT .v-title {{ color: #d97706; }}
.verdict-WATCH {{ background: {p['secondary']}; border: 1px solid {p['card_border']}; color: {p['text']}; }}
.verdict-no_data {{ background: rgba(239, 68, 68, 0.10); border: 1px solid #ef4444; }}
</style>
"""


def _fmt_duration(minutes: int | None) -> str:
    if not minutes:
        return "—"
    h, m = divmod(int(minutes), 60)
    return f"{h} ч {m:02d} м" if h else f"{m} м"


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y")
    except ValueError:
        return iso


def _fmt_dt(iso: str | None) -> str:
    """Дата-время из ISO (новости/снапшоты) -> 'дд.мм, чч:мм'."""
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m, %H:%M")
    except ValueError:
        return iso[:16].replace("T", " ")


def _themed_chart(charts: list, dark: bool) -> None:
    """Altair-график в цветах текущей темы.

    Тёмная тема у нас — CSS-инъекция, а Vega по умолчанию красит график
    в базовую (светлую) тему Streamlit: получалась белая подложка на тёмной
    странице. Рисуем с прозрачным фоном и явно красим оси/сетку/легенду.
    """
    txt = "#fafafa" if dark else "#1f2328"
    grid = "rgba(139, 148, 158, 0.25)" if dark else "rgba(87, 96, 106, 0.2)"
    layered = (
        alt.layer(*charts)
        .configure_axis(labelColor=txt, titleColor=txt, gridColor=grid,
                        domainColor=txt, tickColor=txt)
        .configure_legend(labelColor=txt, titleColor=txt)
        .configure_view(stroke=None)
        .configure(background="transparent")
    )
    st.altair_chart(layered, theme=None, width="stretch")


# ---------------------------------------------------------------- сайдбар

with st.sidebar:
    page = st.radio("Навигация", ["🔍 Поиск цен", "🗓 Календарь", "🤖 AI-помощник"], label_visibility="collapsed")
    st.divider()
    dark = st.toggle("🌙 Тёмная тема")
    st.divider()
    st.caption("🔧 Скоро: ML-прогноз «когда покупать» (CatBoost) по накопленной истории.")

st.markdown(_theme_css(_PALETTES["dark" if dark else "light"]), unsafe_allow_html=True)

# ---------------------------------------------------------------- страница: поиск

if page == "🔍 Поиск цен":
    st.markdown(
        '<div class="hero">'
        "<h1>✈️ Flight Ticket Advisor</h1>"
        "<p>Когда покупать билет: календарь цен по датам вылета, дайджест новостей "
        "региона назначения и рекомендация.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    entries = load_entries()
    _codes = [e["code"] for e in entries]
    _labels = {e["code"]: e["label"] for e in entries}

    # Откуда/куда/валюта — ВНЕ формы: список «Куда» сразу исключает
    # выбранный «Откуда» (перерисовка без сабмита). По умолчанию — моки
    # «Откуда» / «Куда»; конкретный аэропорт выбирается из списка.
    c1, c2, c3 = st.columns(3)
    origin_options = _codes + [OTHER]
    _origin_labels = {**_labels, OTHER: "Откуда"}
    origin_sel = c1.selectbox("Откуда", origin_options,
                              index=len(origin_options) - 1,
                              format_func=lambda c: _origin_labels[c])
    dest_options = [c for c in _codes if c != origin_sel] + [OTHER]
    _dest_labels = {**_labels, OTHER: "Куда"}
    dest_sel = c2.selectbox("Куда", dest_options,
                            index=len(dest_options) - 1,
                            format_func=lambda c: _dest_labels[c])
    currency = c3.selectbox("Валюта", ["RUB", "USD", "EUR"], index=0)

    with st.form("query"):
        d1, d2 = st.columns(2)
        # Диапазон выбора дат: сегодня → +365 дней. Даты до сегодня серые
        # и некликабельны (движок date_input сам блокирует вне min/max),
        # год в навигации календаря ограничен 2026–2027.
        _today = date.today()
        _date_max = _today + timedelta(days=365)
        # value=None: поле пустое до явного выбора пользователем (мок),
        # формат DD.MM.YYYY; заполненность проверяем при сабмите.
        dep_start = d1.date_input(
            "Дата вылета", value=None, format="DD.MM.YYYY",
            min_value=_today, max_value=_date_max,
        )
        dep_end = d2.date_input(
            "Дата вылета (по) — диапазон, необязательно",
            value=None, format="DD.MM.YYYY",
            min_value=_today, max_value=_date_max,
            help="Заполните, чтобы сравнить цены по диапазону дат. "
                 "Если дата вылета известна точно — оставьте пустым.",
        )

        round_trip = st.toggle("↩️ Туда — обратно", value=True,
                               help="Цена — итоговая за оба перелёта; обратный рейс подбирается в Google Flights.")
        if round_trip:
            ret = st.date_input(
                "Дата возвращения", value=None, format="DD.MM.YYYY",
                min_value=_today, max_value=_date_max,
            )
        else:
            ret = None

        p1, p2, p3 = st.columns(3)
        adults = p1.number_input("🧑 Взрослые (12+)", min_value=1, max_value=9, value=1, step=1)
        children = p2.number_input("🧒 Дети (2–11)", min_value=0, max_value=8, value=0, step=1)
        infants = p3.number_input("👶 Младенцы (до 2, на руках)", min_value=0, max_value=4, value=0, step=1,
                                  help="Младенцы без своего места (на коленях) — как принято у большинства авиакомпаний.")

        submitted = st.form_submit_button("Показать цены", type="primary")

    if submitted:
        if origin_sel == OTHER or dest_sel == OTHER:
            st.error("Укажите аэропорты вылета и прилёта — выберите из списка.")
        elif dep_start is None:
            st.error("Укажите дату вылета.")
        elif dep_end is not None and dep_end < dep_start:
            st.error("Дата «по» должна быть не раньше даты вылета.")
        elif round_trip and ret is None:
            st.error("Укажите дату возвращения или выключите «Туда — обратно».")
        elif round_trip and ret <= (dep_end or dep_start):
            st.error("Дата возвращения должна быть позже даты вылета.")
        else:
            dep_end_eff = dep_end or dep_start  # диапазон не указан → одна дата
            pax_key = f"{adults}-{children}-{infants}"
            with st.spinner("Запрашиваю Google Flights (может занять ~10–60 сек)…"):
                res = recommend(
                    origin_sel, dest_sel, dep_start, dep_end_eff, currency,
                    return_date=ret, adults=adults, children=children, infants=infants,
                )
            # Новости региона назначения — рядом с ценой (кэш 1 час).
            city, country = region_for(dest_sel)
            res["_region"] = {"city": city, "country": country}
            if country:
                try:
                    with st.spinner("Собираю новости региона…"):
                        res["news"] = get_news(country, city)
                except Exception:  # noqa: BLE001 — новости не должны ломать страницу
                    res["news"] = None

            # Динамика цены + занятость календаря + итоговый вердикт (orchestrator).
            # Занятость берём на ВСЁ окно поездки: вылет → возвращение (или конец
            # диапазона вылетов) — предупредить и про обратный путь.
            busy_map = None
            if calendar_engine.has_data():
                trip_to = ret or dep_end_eff
                busy_map = calendar_engine.busy_dates(dep_start.isoformat(), trip_to.isoformat())
                res["_busy"] = busy_map
            if res.get("cheapest_date"):
                res["dynamics"] = dynamics(
                    origin_sel, dest_sel, res["cheapest_date"]["dep_date"],
                    ret.isoformat() if ret else None, passengers=pax_key,
                )
                res["verdict"] = make_verdict(
                    res, res.get("news"), res["dynamics"], busy=busy_map
                )
            st.session_state["last_result"] = res

    res = st.session_state.get("last_result")
    if res:
        cal = res["calendar"]
        currency = res["currency"]
        ret_iso = res.get("return_date")
        pax = res.get("passengers") or {}
        pax_parts = []
        if pax.get("adults"):
            pax_parts.append(f"{pax['adults']} взр.")
        if pax.get("children"):
            pax_parts.append(f"{pax['children']} дет.")
        if pax.get("infants"):
            pax_parts.append(f"{pax['infants']} мл.")
        pax_label = ", ".join(pax_parts) if pax_parts else "1 взр."
        pax_key = f"{pax.get('adults', 1)}-{pax.get('children', 0)}-{pax.get('infants', 0)}"

        # --- предупреждение о встречах в окно поездки (VK Workspace) ---
        trip_busy = res.get("_busy")
        if trip_busy:
            lines = "\n".join(
                f"- **{_fmt_date(d)}** — "
                + "; ".join(f"{e['title']} ({e['time']})" for e in evs)
                for d, evs in sorted(trip_busy.items())
            )
            st.warning(
                "📅 **У вас на даты поездки запланированы события:**\n\n"
                f"{lines}\n\nПроверьте, не конфликтуют ли они с вылетом и прилётом."
            )

        df = pd.DataFrame(
            {"date": pd.to_datetime([c["dep_date"] for c in cal]),
             "price": [c["price"] for c in cal]}
        )
        priced_df = df.dropna(subset=["price"])

        if priced_df.empty:
            # Различаем «рейсы найдены, но без цен» (перевозчик не отдаёт цены
            # в Google Flights — например, MOW→BAX) от полного отсутствия данных.
            unpriced = [c for c in cal if (c.get("unpriced_offers") or 0) > 0]
            if unpriced:
                dates = ", ".join(c["dep_date"] for c in unpriced)
                st.warning(
                    "⚠️ **Google Flights нашёл рейсы по этому маршруту, но не отдаёт "
                    "по ним цены** (перевозчик не представлен в источнике цен).\n\n"
                    f"Рейсы без цены есть на даты: {dates}.\n\n"
                    "Проверить цены и купить можно вручную по ссылке ниже — "
                    "сам поиск на платформе работает только с маршрутами, "
                    "по которым источник отдаёт цены."
                )
                st.link_button(
                    "🔗 Открыть маршрут в Google Flights",
                    build_search_url(
                        res["origin"], res["dest"], cal[0]["dep_date"],
                        return_date=ret_iso, currency=currency,
                        adults=pax.get("adults", 1),
                        children=pax.get("children", 0),
                        infants=pax.get("infants", 0),
                    ),
                    help="Тот же поиск, из которого платформа получает данные.",
                )
            else:
                st.warning(
                    "Не удалось получить цены ни на одну дату — попробуйте позже "
                    "или другую валюту."
                )
        else:
            prices = priced_df["price"]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Минимальная", f"{int(prices.min()):,} {currency}")
            m2.metric("Средняя", f"{int(prices.mean()):,} {currency}")
            m3.metric("Максимальная", f"{int(prices.max()):,} {currency}")
            m4.metric("Дороговизна макс.", f"+{int((prices.max() / prices.min() - 1) * 100)}%")

            cheapest = res["cheapest_date"]
            offer = cheapest.get("cheapest_offer") if cheapest else None

            # --- вердикт orchestrator'а + карточка лучшего рейса + ссылка ---
            st.subheader("🎯 Рекомендация")
            v = res.get("verdict")
            if v:
                conf_ru = {"low": "низкая", "medium": "средняя", "high": "высокая"}[v.get("confidence", "low")]
                st.markdown(
                    f'<div class="verdict verdict-{v["signal"]}">'
                    f'<div class="v-title">{v["title"]}</div>'
                    f'<div class="v-text">{v["text"]}</div>'
                    f'<div class="v-conf">Уверенность: {conf_ru}</div>'
                    "</div>",
                    unsafe_allow_html=True,
                )
            if offer:
                seg0 = offer["segments"][0] if offer.get("segments") else {}
                flight_no = seg0.get("flight_no") or (offer.get("airlines") or ["?"])[0]
                airline = seg0.get("airline") or (offer.get("airlines") or ["?"])[0]
                st.markdown(
                    f'<div class="flight-card">'
                    f'<div class="route">{offer.get("from", "")} → {offer.get("to", "")}'
                    f' &nbsp;·&nbsp; {flight_no} ({airline})</div>'
                    f'<div class="price">{offer["price"]:,} {currency}</div>'
                    f'<div class="meta">Вылет {_fmt_date(offer.get("dep_date"))} '
                    f'{offer.get("dep_time", "")} → прилёт {_fmt_date(offer.get("arr_date"))} '
                    f'{offer.get("arr_time", "")} · в пути {_fmt_duration(offer.get("duration_min"))} · '
                    f'пересадок: {offer.get("stops", 0)}'
                    + (" · туда-обратно (итого)" if ret_iso else "")
                    + f" · цена за всех пассажиров ({pax_label})"
                    + "</div></div>",
                    unsafe_allow_html=True,
                )
                gf_url = build_search_url(
                    res["origin"], res["dest"], cheapest["dep_date"],
                    return_date=ret_iso, currency=currency,
                    adults=pax.get("adults", 1),
                    children=pax.get("children", 0),
                    infants=pax.get("infants", 0),
                )
                st.link_button(
                    "🔗 Открыть в Google Flights",
                    gf_url,
                    help="Тот же поиск (с вашим составом пассажиров), из которого "
                         "получены цены; здесь можно выбрать обратный рейс и купить.",
                )
                with st.expander("Сегменты рейса — перелёт туда" if ret_iso else "Сегменты рейса"):
                    if ret_iso:
                        st.caption(
                            "Google Flights показывает детали перелёта туда; обратный "
                            "рейс выбирается по ссылке выше — итоговая цена уже "
                            "включает оба перелёта."
                        )
                    seg_df = pd.DataFrame(
                        [
                            {
                                "Рейс": s.get("flight_no"),
                                "Авиакомпания": s.get("airline"),
                                "Откуда": s.get("from"),
                                "Куда": s.get("to"),
                                "Вылет": f"{_fmt_date(s.get('dep_date'))} {s.get('dep_time') or ''}",
                                "В пути": _fmt_duration(s.get("duration_min")),
                                "Самолёт": s.get("aircraft"),
                            }
                            for s in offer.get("segments", [])
                        ]
                    )
                    st.dataframe(seg_df, width="stretch", hide_index=True)
            elif cheapest:
                st.success(
                    f"**Самая низкая цена сейчас — {cheapest['dep_date']}: "
                    f"{cheapest['price']:,} {currency}**"
                )

            # Критические новости (⚠️) — горизонтальная бегущая строка:
            # всегда на виду, кликабельные заголовки, пауза при наведении.
            _news = res.get("news")
            if _news and _news.get("risk_level") == "warning":
                _crit = [d for d in _news.get("digest", []) if d.get("risk")]
                if not _crit:  # критика есть в warnings, но выпала из дайджеста
                    _crit = [{"title": w["title"], "url": w.get("url", "")}
                             for w in _news.get("warnings", [])]
                if _crit:
                    _line = " &nbsp;•&nbsp; ".join(
                        f'⚠️ <a href="{d.get("url", "")}" target="_blank">{d["title"]}</a>'
                        for d in _crit
                    )
                    st.markdown(
                        f'<div class="news-marquee"><div class="track">{_line}</div></div>',
                        unsafe_allow_html=True,
                    )
                    st.caption(
                        "⚠️ Критические события в регионе назначения — могут "
                        "повлиять на цены и расписание."
                    )
            st.caption(
                "⚠️ Полный прогноз «когда покупать» (CatBoost) появится после "
                "накопления истории цен — сейчас это эвристика."
            )

            # --- новости региона назначения (некритичные — свернутый раздел) ---
            news = res.get("news")
            region = res.get("_region") or {}
            if news is not None:
                region_title = region.get("country") or news.get("region", "регион")
                if region.get("city"):
                    region_title += f" · {region['city']}"
                with st.expander(f"📰 Новости: {region_title}"):
                    if news["digest"]:
                        for d in news["digest"]:
                            risk = " ⚠️" if d.get("risk") else ""
                            st.markdown(
                                f'<div class="news-item">'
                                f'<a href="{d["url"]}" target="_blank">{d["title"]}</a>{risk}'
                                f'<br><span class="src">{d["source"]} · {_fmt_dt(d.get("published"))}'
                                + (f" · {d['risk']}" if d.get("risk") else "")
                                + "</span></div>",
                                unsafe_allow_html=True,
                            )
                        st.caption(
                            "Дайджест: The Guardian + ТАСС, последние дни. Опасные события "
                            "(катаклизмы, беспорядки) отмечены ⚠️ и дублируются бегущей "
                            "строкой над результатами."
                        )
                    else:
                        st.caption("Свежих новостей по региону не найдено.")

            # --- прогноз цены: от сегодня до даты вылета ---
            st.divider()
            st.subheader("📈 Прогноз цены до вылета")
            st.caption(
                "Ожидаемое изменение минимальной цены на выбранную дату вылета "
                "от сегодняшнего дня до вылета: по кривой видно, когда цена "
                "минимальна (время покупки), а когда максимальна. Серая пунктирная "
                "линия — прогноз (эвристическая модель по типовой кривой "
                "бронирования), серая полоса — коридор неопределённости, "
                "оранжевые точки — реальные измерения цены (поллер, каждые 4 ч)."
            )
            hist_dates = [c["dep_date"] for c in cal]
            hist_sel = st.selectbox("Дата вылета", hist_dates, index=0)
            ret_hist = ret_iso if ret_iso else None
            hist = db.price_history(res["origin"], res["dest"], hist_sel, ret_hist,
                                    passengers=pax_key)
            dyn_sel = dynamics(res["origin"], res["dest"], hist_sel, ret_hist,
                               passengers=pax_key)
            st.caption(dyn_sel["text"])

            # Якорь прогноза — текущая реальная цена на эту дату вылета.
            anchor = next((c["price"] for c in cal
                           if c["dep_date"] == hist_sel and c.get("price")), None)
            risk = (res.get("news") or {}).get("risk_level") == "warning"
            fc = (forecast_curve(anchor, date.today(), date.fromisoformat(hist_sel),
                                 risk_warning=risk)
                  if anchor else None)

            today_ts = pd.Timestamp.now().floor("D")
            dep_ts = pd.Timestamp(hist_sel)
            x_axis = alt.Axis(title=None, format="%d.%m.%Y", labelAngle=0)
            layers = []
            if fc:
                fdf = pd.DataFrame(fc["points"])
                fdf["time"] = pd.to_datetime(fdf["date"])
                _x = alt.X("time:T", scale=alt.Scale(domain=[today_ts, dep_ts]),
                           axis=x_axis)
                # Коридор неопределённости.
                layers.append(
                    alt.Chart(fdf).mark_area(opacity=0.15, color="#94a3b8").encode(
                        x=_x, y=alt.Y("low:Q"), y2="high:Q",
                    )
                )
                # Сам прогноз: пунктир + точки.
                _y = alt.Y("price:Q", scale=alt.Scale(zero=False),
                           axis=alt.Axis(title=f"Мин. цена, {currency}"))
                layers.append(
                    alt.Chart(fdf).mark_line(strokeDash=[5, 5], color="#64748b",
                                             point=alt.OverlayMarkDef(color="#94a3b8",
                                                                      size=40))
                    .encode(x=_x, y=_y)
                )
                # Метки ожидаемого минимума и максимума.
                for p, label, color in ((fc["min"], "мин.", "#22c55e"),
                                        (fc["max"], "макс.", "#ef4444")):
                    mdf = pd.DataFrame({"time": [pd.Timestamp(p["date"])],
                                        "price": [p["price"]]})
                    layers.append(
                        alt.Chart(mdf).mark_point(filled=True, size=120, color=color)
                        .encode(x="time:T", y="price:Q")
                    )
                    layers.append(
                        alt.Chart(mdf).mark_text(
                            text=f"{label} ≈ {p['price']:,}", dy=-14, fontSize=11,
                            color=color,
                        ).encode(x="time:T", y="price:Q")
                    )
                # Реальные измерения поверх прогноза.
                if hist:
                    rdf = pd.DataFrame(
                        {"time": pd.to_datetime([h["fetched_at"] for h in hist],
                                                format="ISO8601"),
                         "price": [h["price"] for h in hist]}
                    )
                    layers.append(
                        alt.Chart(rdf).mark_point(filled=True, size=90,
                                                  color="#f59e0b")
                        .encode(x="time:T", y="price:Q")
                    )
                _themed_chart(layers, dark)
                st.caption(forecast_summary(fc, anchor, currency))
                st.caption(
                    "🧪 Прогноз — демо: снапшоты реальных цен копятся только с "
                    "24.10.2026, поэтому кривая построена эвристической моделью "
                    "(типовая кривая бронирования, привязана к текущей цене "
                    "Google Flights). CatBoost-модель на накопленной истории "
                    "появится автоматически."
                )
            elif hist:
                # Прогноза нет (нет цены-якоря) — показываем фактические точки.
                hdf = pd.DataFrame(
                    {"time": pd.to_datetime([h["fetched_at"] for h in hist],
                                            format="ISO8601"),
                     "price": [h["price"] for h in hist]}
                )
                _themed_chart([
                    alt.Chart(hdf).mark_line(point=True, color="#f59e0b").encode(
                        x=alt.X("time:T",
                                scale=alt.Scale(domain=[today_ts, dep_ts]),
                                axis=x_axis),
                        y=alt.Y("price:Q", scale=alt.Scale(zero=False)),
                    )
                ], dark)
                st.caption(
                    f"Период: сегодня → {_fmt_date(hist_sel)} (вылет) · "
                    f"измерений: {len(hist)}"
                )
            else:
                st.caption(
                    "Измерений пока нет — история копится фоновым поллером "
                    "каждые 4 часа."
                )

            # --- история за прошлый год (демо) — доп. справочный график ---
            demo = currency == "RUB" and ensure_demo_history(
                res["origin"], res["dest"], hist_sel, ret_hist, passengers=pax_key
            )
            if demo:
                with st.expander("🗓 История цены за прошлый год (демо-данные)"):
                    hist_all = db.price_history(
                        res["origin"], res["dest"], hist_sel, ret_hist,
                        passengers=pax_key,
                    )
                    if len(hist_all) >= 2:
                        ydf = pd.DataFrame(
                            {"time": pd.to_datetime([h["fetched_at"] for h in hist_all],
                                                    format="ISO8601"),
                             "price": [h["price"] for h in hist_all],
                             "source": ["демо-история" if h.get("synthetic")
                                        else "реальные измерения" for h in hist_all]}
                        ).sort_values("time")
                        y_min = dep_ts - pd.Timedelta(days=365)
                        base = alt.Chart(ydf).encode(
                            x=alt.X("time:T", scale=alt.Scale(domain=[y_min, dep_ts]),
                                    axis=alt.Axis(title=None, format="%d.%m.%Y",
                                                  labelAngle=0)),
                            y=alt.Y("price:Q", scale=alt.Scale(zero=False),
                                    axis=alt.Axis(title=f"Мин. цена, {currency}")),
                        )
                        ylayers = [
                            base.mark_line(color="#f59e0b"),
                            alt.Chart(pd.DataFrame({"time": [today_ts]}))
                            .mark_rule(strokeDash=[5, 5], color="#64748b")
                            .encode(x="time:T"),
                            base.mark_point(filled=True, size=70).encode(
                                color=alt.Color(
                                    "source:N",
                                    scale=alt.Scale(
                                        domain=["реальные измерения", "демо-история"],
                                        range=["#f59e0b", "#94a3b8"],
                                    ),
                                    legend=alt.Legend(title=None),
                                ),
                            ),
                        ]
                        _themed_chart(ylayers, dark)
                        st.caption(
                            f"Период: {_fmt_date(y_min.date().isoformat())} → "
                            f"{_fmt_date(hist_sel)} (вылет) · точек: {len(hist_all)}. "
                            "🧪 Серые точки — синтетическая демо-история "
                            "(сезонность + рост цены ближе к вылету): Google "
                            "Flights не отдаёт исторические цены."
                        )

            # --- таблица деталей ---
            busy_map_tbl = res.get("_busy")
            table = pd.DataFrame(
                {
                    "Дата вылета": [c["dep_date"] for c in cal],
                    "Мин. цена": [
                        f"{c['price']:,} {currency}" if c["price"] is not None else "—"
                        for c in cal
                    ],
                    "Авиакомпания": [
                        ", ".join(c.get("airlines") or []) or "—" for c in cal
                    ],
                    "Рейс": [
                        (c.get("cheapest_offer") or {}).get("segments", [{}])[0].get("flight_no")
                        if c.get("cheapest_offer") else "—"
                        for c in cal
                    ],
                    "Календарь": [
                        (
                            f"занято ({len(busy_map_tbl[c['dep_date']])})"
                            if c["dep_date"] in busy_map_tbl
                            else "свободно"
                        )
                        if busy_map_tbl is not None
                        else "—"
                        for c in cal
                    ],
                }
            )
            st.dataframe(table, width="stretch", hide_index=True)
            fetched = [c["fetched_at"] for c in cal if c.get("fetched_at")]
            if fetched:
                st.caption(f"Данные обновлены: {max(fetched)[:16].replace('T', ' ')}")

# ---------------------------------------------------------------- страница: календарь

elif page == "🗓 Календарь":
    st.title("🗓 Календарь (VK Workspace)")
    # По умолчанию пусто: события показываются только после поиска —
    # и обновляются с каждым новым запросом (окно вылет → возвращение).
    res = st.session_state.get("last_result")
    if not res or res.get("_busy") is None:
        st.caption(
            "События из календаря VK Workspace появятся при выборе дат — "
            "выполните поиск на странице «🔍 Поиск цен»."
        )
    else:
        ret_iso = res.get("return_date")
        start = res["calendar"][0]["dep_date"]
        end = ret_iso or res["calendar"][-1]["dep_date"]
        st.caption(
            f"Окно последнего поиска: {_fmt_date(start)} → {_fmt_date(end)}."
        )
        busy = res["_busy"]
        if busy:
            for d, evs in sorted(busy.items()):
                evs_txt = "; ".join(f"{e['title']} ({e['time']})" for e in evs)
                st.markdown(
                    f'<div class="news-item"><b>{_fmt_date(d)}</b> — {evs_txt}</div>',
                    unsafe_allow_html=True,
                )
            st.caption(
                "События по снапшоту календаря VK Workspace. При поиске билетов "
                "даты вылета с встречами помечаются в таблице дат, а "
                "предупреждение о конфликтах показывается над результатами."
            )
        else:
            st.caption("Занятых встреч в этом окне нет — можно вылетать в любой день.")

# ---------------------------------------------------------------- страница: чат

else:
    st.title("🤖 AI-помощник")
    st.caption(
        "Задайте любой вопрос — помощник отвечает по своим знаниям и может САМ "
        "запустить поиск цен по любому маршруту и датам (например: «сколько стоит "
        "слетать вдвоём в Нью-Йорк на Рождество на неделю?»). Открытый на платформе "
        "поиск используется только если он релевантен вопросу."
    )

    if "chat" not in st.session_state:
        st.session_state.chat = []

    for m in st.session_state.chat:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    question = st.chat_input("Спросите что угодно — например: «Когда выгоднее купить билет в Токио?»")
    if question:
        st.session_state.chat.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            try:
                with st.spinner("Думаю…"):
                    answer = chat_engine.ask(
                        question,
                        st.session_state.chat[:-1],
                        st.session_state.get("last_result"),
                    )
                if answer:
                    st.session_state.chat.append({"role": "assistant", "content": answer})
                    st.markdown(answer)
            except Exception as e:  # noqa: BLE001 — показываем ошибку пользователю
                st.error(f"AI-помощник недоступен: {e}")
