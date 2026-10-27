"""Бенчмарк LLM-провайдеров AI-помощника на синтетике (bench/cases.py).

Использует боевые промпты и описание инструмента из chat_engine; Google
Flights не вызывается (результат search_prices подставляется из кейса).

Запуск из корня проекта (ключи — в .streamlit/secrets.toml, .env или окружении):
    .venv/bin/python -m bench.run_llm_bench
    .venv/bin/python -m bench.run_llm_bench --providers anthropic,yandex:yandexgpt-lite/latest --repeats 3

Результат: bench/results/llm_bench.json (сырые ответы + метрики) и
bench/results/llm_bench.md (таблица для README).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(ROOT / ".env")

from backend.app.engines import chat_engine, yandex_gpt  # noqa: E402
from bench.cases import (  # noqa: E402
    CITY, DIGEST_CASES, GROUNDING_CASES, NO_TOOL_CASES, TODAY, TOOL_CASES,
)

RESULTS_DIR = ROOT / "bench" / "results"
SYSTEM = chat_engine._system_prompt(None, today=TODAY)


# ------------------------------------------------------------------ провайдеры

class Provider:
    """Единый интерфейс: chat(messages, tools) -> {text, tool_calls, usage, latency}.

    messages — нейтральный формат:
      {"role": "user"|"assistant", "text": str}
      {"role": "assistant", "tool_call": {"name", "arguments"}}
      {"role": "tool", "name": str, "content": str}
    """

    def __init__(self, spec: str):
        kind, _, model = spec.partition(":")
        self.kind = kind
        if kind == "anthropic":
            self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
        elif kind == "yandex":
            self.model = model or os.environ.get("YANDEX_GPT_MODEL", "yandexgpt/latest")
        else:
            raise ValueError(f"Неизвестный провайдер: {spec}")
        self.name = f"{kind}:{self.model}"

    def available(self) -> str | None:
        if self.kind == "anthropic" and not chat_engine._anthropic_ready():
            return "нет ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN"
        if self.kind == "yandex" and not yandex_gpt.ready():
            return "нет YANDEX_GPT_API_KEY / YANDEX_GPT_FOLDER_ID"
        return None

    def chat(self, system: str, messages: list[dict], tools: bool, max_tokens: int = 1024) -> dict:
        t0 = time.perf_counter()
        out = (self._anthropic if self.kind == "anthropic" else self._yandex)(
            system, messages, tools, max_tokens)
        out["latency"] = time.perf_counter() - t0
        return out

    def _anthropic(self, system, messages, tools, max_tokens) -> dict:
        msgs = []
        for i, m in enumerate(messages):
            if m["role"] == "tool":
                msgs.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": f"toolu_bench_{i - 1}",
                     "content": m["content"]}]})
            elif "tool_call" in m:
                msgs.append({"role": "assistant", "content": [
                    {"type": "tool_use", "id": f"toolu_bench_{i}",
                     "name": m["tool_call"]["name"], "input": m["tool_call"]["arguments"]}]})
            else:
                msgs.append({"role": m["role"], "content": m["text"]})
        kwargs = {"tools": chat_engine._TOOLS} if tools else {}
        resp = chat_engine._client().messages.create(
            model=self.model, max_tokens=max_tokens, system=system, messages=msgs, **kwargs)
        return {
            "text": "".join(b.text for b in resp.content if b.type == "text").strip(),
            "tool_calls": [{"name": b.name, "arguments": dict(b.input)}
                           for b in resp.content if b.type == "tool_use"],
            "usage": {"input_tokens": resp.usage.input_tokens,
                      "output_tokens": resp.usage.output_tokens},
            "tools_dropped": False,
        }

    def _yandex(self, system, messages, tools, max_tokens) -> dict:
        msgs = [{"role": "system", "text": system}]
        for m in messages:
            if m["role"] == "tool":
                msgs.append({"role": "user", "toolResultList": {"toolResults": [
                    {"functionResult": {"name": m["name"], "content": m["content"]}}]}})
            elif "tool_call" in m:
                msgs.append({"role": "assistant", "toolCallList": {"toolCalls": [
                    {"functionCall": m["tool_call"]}]}})
            else:
                msgs.append({"role": m["role"], "text": m["text"]})
        turn = yandex_gpt.complete(
            msgs, tools=chat_engine._yandex_tools() if tools else None,
            max_tokens=max_tokens, model=self.model)
        return {"text": turn.text, "tool_calls": turn.tool_calls,
                "usage": turn.usage, "tools_dropped": turn.tools_dropped}


# ------------------------------------------------------------------ оценщики

def _day(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _date_ok(got, exp) -> bool:
    g = _day(got)
    if g is None:
        return False
    if isinstance(exp, tuple):
        return _day(exp[0]) <= g <= _day(exp[1])
    return g == _day(exp)


def score_tool(case: dict, out: dict) -> dict:
    exp = case["expect"]
    calls = [c for c in out["tool_calls"] if c["name"] == "search_prices"]
    if not calls:
        return {"called": False, "fields": {}, "all_ok": False}
    a = calls[0]["arguments"]
    f: dict[str, bool] = {}
    f["origin"] = str(a.get("origin", "")).upper() in CITY[exp["origin"]]
    f["dest"] = str(a.get("dest", "")).upper() in CITY[exp["dest"]]
    f["dep_date"] = _date_ok(a.get("dep_date"), exp["dep"])
    if "dep_end" in exp:
        f["dep_end"] = _date_ok(a.get("dep_end"), exp["dep_end"])
    if "ret" in exp:
        ret, got = exp["ret"], a.get("return_date")
        if ret is None:
            f["return_date"] = got in (None, "")
        elif isinstance(ret, tuple) and ret[0] == "offset":
            g, d = _day(got), _day(a.get("dep_date"))
            f["return_date"] = bool(g and d and ret[1] <= (g - d).days <= ret[2])
        else:
            f["return_date"] = _date_ok(got, ret)
    for k in ("adults", "children", "infants"):
        default = 1 if k == "adults" else 0
        want = exp.get(k, default)
        f[k] = int(a.get(k) or default) == want
    if "currency" in exp:
        f["currency"] = str(a.get("currency", "")).upper() == exp["currency"]
    return {"called": True, "fields": f, "all_ok": all(f.values()), "arguments": a}


_PRICE_RE = re.compile(r"\d{1,3}(?:[\s\u00a0\u202f.,]\d{3})+|\d{4,}")
_RU_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]
_HONEST = ["не удалось", "нет цен", "не вернул", "не найден", "недоступ", "не отда",
           "отсутству", "не получил", "нет данных", "не нашл", "нет информации"]


def _numbers(text: str) -> list[int]:
    return [int(re.sub(r"\D", "", m)) for m in _PRICE_RE.findall(text)]


def score_grounding(case: dict, out: dict) -> dict:
    text = out["text"]
    if case["cheapest"] is None:
        low = text.lower()
        return {"ok": any(h in low for h in _HONEST), "kind": "honest_no_data"}
    price, d = case["cheapest"]["price"], _day(case["cheapest"]["date"])
    nums = _numbers(text)
    price_ok = price in nums
    date_forms = [d.isoformat(), d.strftime("%d.%m"), f"{d.day} {_RU_MONTHS[d.month - 1]}",
                  f"{d.day:02d} {_RU_MONTHS[d.month - 1]}"]
    date_ok = any(f in text for f in date_forms)
    calendar_prices = {c["min_price"] for c in case["result"]["price_calendar"]}
    # «Выдуманная» цена — 4+-значное число в диапазоне цен, которого нет в календаре
    # (годы, кол-во дней и т.п. отсекаются диапазоном).
    lo, hi = min(calendar_prices) * 0.5, max(calendar_prices) * 2
    invented = [n for n in nums if lo <= n <= hi and n not in calendar_prices
                and n != sum(calendar_prices) // len(calendar_prices)]
    return {"ok": price_ok and date_ok and not invented, "price_ok": price_ok,
            "date_ok": date_ok, "invented": invented, "kind": "priced"}


def score_digest(case: dict, out: dict) -> dict:
    text = out["text"].strip()
    sentences = [s for s in re.split(r"(?<=[.!?…])\s+", text) if len(s.strip()) > 3]
    n = len(sentences)
    no_md = not re.search(r"(^|\n)\s*([-*#•]|\d+\.)\s|\*\*|__", text)
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text)
    cyr = sum(1 for ch in letters if re.match(r"[А-Яа-яЁё]", ch)) / max(len(letters), 1)
    low = text.lower()
    risk_ok = True
    if case["risk"]:
        risk_ok = "⚠" in text or any(w in low for w in case["risk_words"])
    checks = {"sentences_2_4": 2 <= n <= 4, "no_markdown": no_md,
              "russian": cyr >= 0.8, "risk_flagged": risk_ok}
    return {"ok": all(checks.values()), "checks": checks, "sentences": n}


# ------------------------------------------------------------------ прогон

def run_case(p: Provider, task: str, case: dict) -> dict:
    rec = {"provider": p.name, "task": task, "id": case["id"]}
    try:
        if task in ("tool", "no_tool"):
            out = p.chat(SYSTEM, [{"role": "user", "text": case["q"]}], tools=True)
        elif task == "grounding":
            out = p.chat(SYSTEM, [
                {"role": "user", "text": case["q"]},
                {"role": "assistant", "tool_call": {"name": "search_prices", "arguments": case["args"]}},
                {"role": "tool", "name": "search_prices",
                 "content": json.dumps(case["result"], ensure_ascii=False)},
            ], tools=True)
        else:
            user = chat_engine.digest_prompt({"digest": case["digest"]}, case["region"])
            out = p.chat(chat_engine.DIGEST_SYSTEM, [{"role": "user", "text": user}],
                         tools=False, max_tokens=400)
    except Exception as e:  # noqa: BLE001 — ошибка провайдера = проваленный кейс
        rec.update(error=f"{type(e).__name__}: {e}"[:300], ok=False)
        return rec
    rec.update(text=out["text"], tool_calls=out["tool_calls"], usage=out["usage"],
               latency=round(out["latency"], 3), tools_dropped=out["tools_dropped"])
    if task == "tool":
        s = score_tool(case, out)
        rec.update(score=s, ok=s["all_ok"])
    elif task == "no_tool":
        rec.update(ok=not out["tool_calls"])
    elif task == "grounding":
        s = score_grounding(case, out)
        rec.update(score=s, ok=s["ok"])
    else:
        s = score_digest(case, out)
        rec.update(score=s, ok=s["ok"])
    return rec


def _pct(xs: list[bool]) -> float | None:
    return round(100 * sum(xs) / len(xs), 1) if xs else None


def _percentile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 2)


def summarize(records: list[dict]) -> dict:
    by_task = lambda t: [r for r in records if r["task"] == t]  # noqa: E731
    tool, no_tool = by_task("tool"), by_task("no_tool")
    tp = sum(1 for r in tool if r.get("tool_calls"))
    fn = len(tool) - tp
    fp = sum(1 for r in no_tool if r.get("tool_calls"))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec_ = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec_ / (prec + rec_) if prec + rec_ else 0.0
    fields: dict[str, list[bool]] = {}
    for r in tool:
        for k, v in (r.get("score") or {}).get("fields", {}).items():
            fields.setdefault(k, []).append(v)
    lat = [r["latency"] for r in records if "latency" in r]
    tok_in = [r["usage"].get("input_tokens", 0) for r in records if r.get("usage")]
    tok_out = [r["usage"].get("output_tokens", 0) for r in records if r.get("usage")]
    ok_all = [r["ok"] for r in records]
    return {
        "n": len(records),
        "errors": sum(1 for r in records if "error" in r),
        "tools_dropped": sum(1 for r in records if r.get("tools_dropped")),
        "routing": {"precision": round(100 * prec, 1), "recall": round(100 * rec_, 1),
                    "f1": round(100 * f1, 1)},
        "tool_args_exact": _pct([r["ok"] for r in tool]),
        "tool_fields": {k: _pct(v) for k, v in fields.items()},
        "no_tool_correct": _pct([r["ok"] for r in no_tool]),
        "grounding": _pct([r["ok"] for r in by_task("grounding")]),
        "digest": _pct([r["ok"] for r in by_task("digest")]),
        "overall": _pct(ok_all),
        "latency_p50": _percentile(lat, 0.5),
        "latency_p95": _percentile(lat, 0.95),
        "latency_mean": round(statistics.mean(lat), 2) if lat else None,
        "tokens_in_mean": round(statistics.mean(tok_in)) if tok_in else None,
        "tokens_out_mean": round(statistics.mean(tok_out)) if tok_out else None,
    }


ROWS = [
    ("Маршрутизация: F1 вызова search_prices, %", lambda s: s["routing"]["f1"]),
    ("Маршрутизация: precision / recall, %",
     lambda s: f'{s["routing"]["precision"]} / {s["routing"]["recall"]}'),
    ("Аргументы инструмента: все поля верны, %", lambda s: s["tool_args_exact"]),
    ("  — даты вылета, %", lambda s: s["tool_fields"].get("dep_date")),
    ("  — дата возвращения, %", lambda s: s["tool_fields"].get("return_date")),
    ("  — пассажиры (взрослые), %", lambda s: s["tool_fields"].get("adults")),
    ("Общие вопросы без лишнего поиска, %", lambda s: s["no_tool_correct"]),
    ("Опора на результат поиска (цена+дата, без выдумки), %", lambda s: s["grounding"]),
    ("Саммари новостей по требованиям, %", lambda s: s["digest"]),
    ("Итого кейсов пройдено, %", lambda s: s["overall"]),
    ("Латентность p50 / p95, с", lambda s: f'{s["latency_p50"]} / {s["latency_p95"]}'),
    ("Токены вход / выход (среднее)", lambda s: f'{s["tokens_in_mean"]} / {s["tokens_out_mean"]}'),
    ("Ошибки API", lambda s: s["errors"]),
]


def to_markdown(summary: dict, meta: dict) -> str:
    names = list(summary)
    head = "| Метрика | " + " | ".join(f"`{n}`" for n in names) + " |\n"
    head += "|---|" + "---|" * len(names) + "\n"
    body = "".join(
        f"| {title} | " + " | ".join(str(fn(summary[n])) for n in names) + " |\n"
        for title, fn in ROWS
    )
    return (f"Прогон {meta['started_at']}, кейсов на провайдера: {meta['cases']} × "
            f"повторов {meta['repeats']}, «сегодня» = {TODAY.isoformat()}.\n\n" + head + body)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default="anthropic,yandex")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", default="", help="подмножество задач: tool,no_tool,grounding,digest")
    args = ap.parse_args()

    tasks = [("tool", TOOL_CASES), ("no_tool", NO_TOOL_CASES),
             ("grounding", GROUNDING_CASES), ("digest", DIGEST_CASES)]
    if args.only:
        keep = set(args.only.split(","))
        tasks = [t for t in tasks if t[0] in keep]
    providers = []
    for spec in args.providers.split(","):
        p = Provider(spec.strip())
        why = p.available()
        if why:
            print(f"[skip] {p.name}: {why}")
            continue
        providers.append(p)
    if not providers:
        sys.exit("Нет доступных провайдеров — задайте ключи в .streamlit/secrets.toml "
                 "(образец: .streamlit/secrets.toml.example)")

    jobs = [(p, t, c) for p in providers for _ in range(args.repeats)
            for t, cases in tasks for c in cases]
    started = datetime.now().isoformat(timespec="seconds")
    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, rec in enumerate(ex.map(lambda j: run_case(*j), jobs), 1):
            records.append(rec)
            mark = "ok " if rec["ok"] else ("ERR" if "error" in rec else "-- ")
            print(f"[{i:>3}/{len(jobs)}] {mark} {rec['provider']:<40} {rec['task']:<9} {rec['id']}")

    summary = {p.name: summarize([r for r in records if r["provider"] == p.name])
               for p in providers}
    meta = {"started_at": started, "today": TODAY.isoformat(), "repeats": args.repeats,
            "cases": sum(len(c) for _, c in tasks)}
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "llm_bench.json").write_text(
        json.dumps({"meta": meta, "summary": summary, "records": records},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    md = to_markdown(summary, meta)
    (RESULTS_DIR / "llm_bench.md").write_text(md, encoding="utf-8")
    print("\n" + md)


if __name__ == "__main__":
    main()
