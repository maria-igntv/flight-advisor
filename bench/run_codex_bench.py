"""Реальный бенчмарк Codex CLI на общих 44 кейсах.

Codex вызывается non-interactive в read-only sandbox. Вместо нативного tool-use
проверяется решение о вызове через строго типизированный structured JSON.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

from backend.app.engines import chat_engine
from bench.cases import DIGEST_CASES, GROUNDING_CASES, NO_TOOL_CASES, TODAY, TOOL_CASES
from bench.run_llm_bench import score_digest, score_grounding, score_tool, summarize, to_markdown

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "tool_calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "arguments_json": {"type": "string"},
                },
                "required": ["name", "arguments_json"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["text", "tool_calls"],
    "additionalProperties": False,
}


def call_codex(prompt: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        schema = Path(tmp) / "schema.json"
        schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
        command = [
            "codex", "exec", "--ephemeral", "--sandbox", "read-only",
            "--model", "gpt-6.1-sol", "--json", "--output-schema", str(schema), "-",
        ]
        started = time.perf_counter()
        process = subprocess.run(
            command, input=prompt, text=True, capture_output=True, cwd=ROOT,
            timeout=180, check=False,
        )
        latency = time.perf_counter() - started
    if process.returncode != 0:
        raise RuntimeError((process.stderr or process.stdout)[-500:])
    text = None
    usage = {}
    for line in process.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "agent_message":
            text = event["item"]["text"]
        if event.get("type") == "turn.completed":
            usage = event.get("usage", {})
        if event.get("type") == "turn.failed":
            raise RuntimeError(event.get("error", {}).get("message", "Codex turn failed"))
    if text is None:
        raise RuntimeError("Codex не вернул agent_message")
    parsed = json.loads(text)
    tool_calls = []
    for call in parsed["tool_calls"]:
        tool_calls.append({"name": call["name"], "arguments": json.loads(call["arguments_json"])})
    return {
        "text": parsed["text"], "tool_calls": tool_calls, "latency": latency,
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
        },
    }


def prompt_for(task: str, case: dict) -> str:
    system = chat_engine._system_prompt(None, today=TODAY)
    if task in ("tool", "no_tool"):
        content = case["q"]
    elif task == "grounding":
        content = (
            f"Вопрос пользователя: {case['q']}\n\nРезультат search_prices:\n"
            f"{json.dumps(case['result'], ensure_ascii=False)}"
        )
    else:
        system = chat_engine.DIGEST_SYSTEM
        content = chat_engine.digest_prompt({"digest": case["digest"]}, case["region"])
    return f"""{system}

Схема доступного инструмента: {json.dumps(chat_engine._TOOLS, ensure_ascii=False)}

{content}

Ответь только JSON по схеме. Если нужен поиск цен, добавь один tool_call с name=search_prices и аргументами строго по схеме инструмента в arguments_json как JSON-строка. Если поиск не нужен, tool_calls=[] и текст ответа в text. Не читай файлы и не выполняй команды."""


def run_case(task: str, case: dict) -> dict:
    record = {"provider": "codex:gpt-6.1-sol", "task": task, "id": case["id"]}
    try:
        out = call_codex(prompt_for(task, case))
        record.update(text=out["text"], tool_calls=out["tool_calls"], usage=out["usage"],
                      latency=round(out["latency"], 3), tools_dropped=False)
        if task == "tool":
            score = score_tool(case, out)
            record.update(score=score, ok=score["all_ok"])
        elif task == "no_tool":
            record["ok"] = not out["tool_calls"]
        elif task == "grounding":
            score = score_grounding(case, out)
            record.update(score=score, ok=score["ok"])
        else:
            score = score_digest(case, out)
            record.update(score=score, ok=score["ok"])
    except Exception as exc:  # noqa: BLE001 — ошибка провайдера становится failed case
        record.update(error=f"{type(exc).__name__}: {exc}"[:500], ok=False)
    return record


def error_analysis(records: list[dict]) -> str:
    failed = [record for record in records if not record["ok"]]
    lines = ["", "## Разбор ошибок", ""]
    if not failed:
        return "\n".join(lines + ["Все кейсы пройдены.", ""])
    lines += ["| Кейс | Задача | Причина / фактический ответ |", "|---|---|---|"]
    for record in failed:
        score = record.get("score") or {}
        wrong_fields = [key for key, ok in score.get("fields", {}).items() if not ok]
        if record.get("error"):
            reason = record["error"]
        elif wrong_fields:
            reason = "Неверные поля: " + ", ".join(wrong_fields)
        elif score.get("invented"):
            reason = "Посторонние числа: " + ", ".join(map(str, score["invented"]))
        elif record["task"] == "no_tool" and record.get("tool_calls"):
            reason = "Лишний вызов инструмента"
        else:
            reason = (record.get("text") or "Некорректный tool call")[:300]
        lines.append(f"| {record['id']} | {record['task']} | {reason.replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    tasks = [("tool", TOOL_CASES), ("no_tool", NO_TOOL_CASES),
             ("grounding", GROUNDING_CASES), ("digest", DIGEST_CASES)]
    if args.only:
        allowed = set(args.only.split(","))
        tasks = [task for task in tasks if task[0] in allowed]
    jobs = [(task, case) for _ in range(args.repeats) for task, cases in tasks for case in cases]
    records = []
    for index, (task, case) in enumerate(jobs, 1):
        record = run_case(task, case)
        records.append(record)
        print(f"[{index}/{len(jobs)}] {'ok' if record['ok'] else 'FAIL'} {task} {case['id']}", flush=True)
    summary = {"codex:gpt-6.1-sol": summarize(records)}
    meta = {"started_at": TODAY.isoformat(), "today": TODAY.isoformat(),
            "repeats": args.repeats, "cases": sum(len(cases) for _, cases in tasks),
            "transport": "Codex CLI structured JSON, not native tool calling"}
    output = ROOT / "bench" / "results" / "llm_bench"
    output.with_suffix(".json").write_text(json.dumps(
        {"meta": meta, "summary": summary, "records": records}, ensure_ascii=False, indent=1), encoding="utf-8")
    output.with_suffix(".md").write_text(
        to_markdown(summary, meta) + error_analysis(records), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
