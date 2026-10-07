"""Self-improvement cycle: notice a defect, change exactly one thing, measure, roll back if worse.

This is the system's first assigned task. It reads its own event log and metrics,
asks the model to name the weakest link, applies one change, tests it on 1-3 real
tasks, compares against the pre-change baseline, and keeps or reverts. Every
outcome is written to improvements.jsonl regardless of result.
"""

import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent import loop, tools
from rag import index as rag
from runtime import llm, state, thermal

STATE_ROOT = state.ROOT

# An effect smaller than this is inside benchmark noise, not a result.
MIN_EFFECT = 0.15

KNOWN_COMPONENTS = {
    "runtime/llm.py",
    "runtime/safety.py",
    "runtime/thermal.py",
    "agent/loop.py",
    "agent/tools.py",
    "rag/index.py",
    "skills/plan.md",
    "skills/orchestrator.md",
    "skills/sub_agent.md",
    "skills/verify.md",
    "skills/plan_trouble.md",
    "skills/self_improve.md",
}


def collect_evidence():
    events = state.read_events(since_minutes=1440, limit=1000)
    metrics = state.load_metrics()
    history = state.read_improvements(limit=20)
    gpu = thermal.check()
    kinds = {}
    for e in events:
        kinds[e.get("kind")] = kinds.get(e.get("kind"), 0) + 1
    failures = [e for e in events if e.get("kind") in ("json_parse_failed", "llm_error", "tool_call_unknown", "tool_call_bad_args")]
    skills = sorted(f[:-3] for f in os.listdir(os.path.join(STATE_ROOT, "skills")) if f.endswith(".md"))
    return {
        "events": events[-60:],
        "event_counts": kinds,
        "metrics": metrics,
        "improvements_history": history[-10:],
        "hardware": {"gpu": gpu, "vram_total_mb": gpu.get("total_mb"), "thresholds": thermal.THRESHOLDS},
        "skills": skills,
        "recent_failures": failures[-10:],
        "rag": rag.stats(),
    }


def measure(client, tasks=None, repeats=3):
    """Run the benchmark several times and return the mean.

    Measured reason for repeats: with a single pass per task the benchmark is
    noisy enough that the same code scored 0.5 on one run and 1.0 on the next.
    A single before/after pair then attributes somebody else's fix to this
    cycle's edit - which is exactly what happened on the first successful run.
    The mean over repeats is what the verdict is based on.
    """
    tasks = tasks or BENCHMARK_TASKS
    per_run, detail = [], []

    for rep in range(repeats):
        ok = 0
        for t in tasks:
            gate, snap, _ = thermal.gate("benchmark")
            for f in t.get("expect", []):
                p = os.path.join(STATE_ROOT, f)
                if os.path.exists(p):
                    os.remove(p)
            t0 = time.time()
            try:
                cycle = loop.run_cycle(t["goal"], client=client, verbose=False)
            except Exception as e:
                detail.append({"run": rep, "task": t["name"],
                               "error": f"{type(e).__name__}: {e}"[:200]})
                continue
            dt = time.time() - t0

            stages = cycle.get("stages") or []
            verified = all(s.get("verified") for s in stages) and bool(stages)
            exists = all(os.path.exists(os.path.join(STATE_ROOT, f)) for f in t.get("expect", []))
            attempts = sum(s.get("attempts", 0) for s in stages)
            good = verified and exists and dt < 240 and cycle.get("status") == "completed"
            if good:
                ok += 1
            detail.append({
                "run": rep,
                "task": t["name"],
                "status": cycle.get("status"),
                "verified": verified,
                "files_written": exists,
                "attempts": attempts,
                "seconds": round(dt, 1),
                "temp_c": snap.get("temp_c"),
            })
        per_run.append(ok / len(tasks) if tasks else 0.0)

    for f in ("state/_bench.json", "state/_bench.md"):
        p = os.path.join(STATE_ROOT, f)
        if os.path.exists(p):
            os.remove(p)

    mean = sum(per_run) / len(per_run) if per_run else 0.0
    spread = max(per_run) - min(per_run) if per_run else 0.0
    return mean, {"runs": per_run, "spread": round(spread, 3), "detail": detail}


BENCHMARK_TASKS = [
    {
        "name": "write-json",
        "goal": 'Создай файл state/_bench.json с полями alpha=1 и beta="два".',
        "expect": ["state/_bench.json"],
    },
    {
        "name": "write-markdown",
        "goal": 'Создай файл state/_bench.md с заголовком "Бенчмарк" и строкой "готово".',
        "expect": ["state/_bench.md"],
    },
]


def propose(client, evidence, benchmark=None, show_thinking=False):
    prompt = "\n".join([
        f"Цикл самоулучшения, {state.now()}",
        f"Счётчики событий: {json.dumps(evidence['event_counts'], ensure_ascii=False)}",
        f"Результат бенчмарка (главный источник правды о том, что реально сломано): "
        f"{json.dumps(benchmark or [], ensure_ascii=False)}",
        f"Последние сбои: {json.dumps(evidence['recent_failures'][-5:], ensure_ascii=False)[:1200]}",
        f"Метрики: {json.dumps(evidence['metrics'], ensure_ascii=False)[:800]}",
        f"Железо: {json.dumps(evidence['hardware'], ensure_ascii=False)}",
        f"История улучшений: {json.dumps(evidence['improvements_history'][-5:], ensure_ascii=False)[:800]}",
        f"Изменять можно только эти файлы: {sorted(KNOWN_COMPONENTS)}",
        "",
        "Назови ОДНО слабое место. Если в бенчмарке есть задача с verified=false или "
        "files_written=false, слабое место - это она, а не то, что теоретически может сломаться.",
        "Опиши правку словом change_instruction - что именно и куда вписать. "
        "Сам файл перепишет субагент через file_write, возвращать его текст не нужно.",
    ])
    # 4000 because a proposal carries the whole new file inside a JSON string;
    # at 900 the reply is cut off mid-file and the cycle reports no_proposal.
    parsed, meta = loop.ask_json(client, "self_improve", prompt, max_tokens=4000,
                                 show_thinking=show_thinking)
    if not parsed:
        state.log_event("self_improve_no_proposal", reason="модель не вернула разбираемый JSON", meta=meta)
        return None
    for key in ("weakness", "component", "change_instruction", "reason", "expected_effect"):
        if not parsed.get(key):
            state.log_event("self_improve_incomplete", missing=key)
            return None
    parsed["_meta"] = meta
    return parsed


def extract_file_content(component, change):
    """Pull new file content out of the proposal, or return None.

    Measured catastrophe this prevents: the first self-improvement run received
    a prose description of a change ("add handling of numeric strings to the
    parser") with no file body, and the naive fallback wrote that prose over
    runtime/llm.py, deleting the whole client. A prose proposal must never be
    able to overwrite code.
    """
    if "<<FILE:" not in change or ">>" not in change:
        return None
    start = change.index("<<FILE:") + len("<<FILE:")
    end = change.index(">>", start)
    body = change[start:end].strip()
    return body + "\n" if body else None


def validate_component(component):
    """Refuse a change that leaves the component unusable.

    Import plus compile is the minimum bar: a file that does not parse, or a
    module that cannot be imported, is never allowed to reach the benchmark.
    """
    path = os.path.join(STATE_ROOT, component)
    if not os.path.exists(path):
        return False, "файл не существует после правки"
    if path.endswith(".py"):
        try:
            compile(open(path, "r", encoding="utf-8").read(), path, "exec")
        except SyntaxError as e:
            return False, f"синтаксическая ошибка: {e}"
        try:
            import importlib

            mod = ".".join(component.split("/")[:-1]) + "." + component.split("/")[-1][:-3]
            importlib.import_module(mod)
            importlib.reload(importlib.import_module(mod))
        except Exception as e:
            return False, f"модуль не импортируется: {type(e).__name__}: {e}"
    if path.endswith(".md"):
        text = open(path, "r", encoding="utf-8").read()
        if len(text.strip()) < 40:
            return False, "skill-файл подозрительно короткий"
    return True, "ok"


def apply_change(proposal, client):
    """Apply the proposed change. Returns (ok, info).

    Measured rationale for delegating the edit instead of inlining a whole file in
    the JSON answer: the model could not reliably emit a complete file inside a
    JSON string, so proposals arrived as prose and either got rejected or, in the
    very first run, overwrote runtime/llm.py with a sentence. Execution through
    the sub-agent's file_write is the path already measured to work.
    """
    component = proposal["component"].replace("\\", "/").strip()
    if component not in KNOWN_COMPONENTS:
        return False, {"error": f"component not on allowlist: {component}"}

    inline = extract_file_content(component, proposal.get("change_instruction") or "")
    if inline is not None:
        result = tools.file_write(component, inline)
        return bool(result.get("ok")), result

    instruction = (
        f"Внеси ровно одну правку в файл {component}.\n\n"
        f"Что исправляем: {proposal['weakness']}\n"
        f"Инструкция: {proposal['change_instruction']}\n"
        f"Почему: {proposal['reason']}\n"
        f"Ожидаемый эффект: {proposal.get('expected_effect', '')}\n\n"
        f"Обязательный порядок: сначала file_read {component}, затем file_write {component} "
        f"с ПОЛНЫМ новым содержимым файла. Не сокращай и не пиши «...» вместо кода. "
        f"Меняй только то, что относится к инструкции; остальное сохрани как есть."
    )
    report, meta = loop.ask_json(client, "sub_agent", instruction, max_tokens=1400, tools_enabled=True)
    dispatched = [r.get("name") for r in (meta.get("tool_results") or [])]
    wrote = [
        r for r in (meta.get("tool_results") or [])
        if r.get("name") == "file_write" and (r.get("result") or {}).get("ok")
    ]
    if not wrote:
        state.log_event("self_improve_rejected_proposal", component=component,
                        reason="субагент не выполнил file_write", tools=dispatched)
        return False, {"error": f"sub-agent made no successful file_write (tools: {dispatched})"}
    return True, {"component": component, "sub_agent": report, "tools": dispatched}


def run_cycle(client=None, max_changes=1, verbose=True, show_thinking=False):
    client = client or llm.Llm()
    started = time.time()
    result = {"started": state.now(), "changes": [], "status": "unknown"}
    state.log_event("self_improve_start")

    if not client.health():
        result["status"] = "no_runtime"
        state.log_event("self_improve_abort", reason="llama-server unreachable")
        if verbose:
            print("LLM недоступен, самоулучшение не запускается.")
        return result

    evidence = collect_evidence()
    if verbose:
        print("доказательства собраны:", json.dumps(evidence["event_counts"], ensure_ascii=False)[:200])

    baseline_score, baseline_detail = measure(client)
    if verbose:
        print(f"baseline: {baseline_score:.2f}  прогоны: {baseline_detail.get('runs')}  "
              f"разброс: {baseline_detail.get('spread')}")
    state.log_event("self_improve_baseline", score=baseline_score, detail=baseline_detail)

    for _ in range(max_changes):
        proposal = propose(client, evidence, benchmark=baseline_detail, show_thinking=show_thinking)
        if not proposal:
            result["status"] = "no_proposal"
            break

        component = proposal["component"]
        if verbose:
            print(f"\nслабое место: {proposal['weakness']}")
            print(f"компонент   : {component}")
            print(f"инструкция  : {proposal['change_instruction'][:200]}")

        backup_path = state.backup(os.path.join(STATE_ROOT, component))
        ok, info = apply_change(proposal, client)
        if not ok:
            state.log_event("self_improve_change_failed", component=component, info=str(info)[:300])
            if verbose:
                print(f"ОТКЛОНЕНО: {info.get('error')}")
            continue

        valid, why = validate_component(component)
        if not valid:
            restored = state.restore(backup_path) if backup_path else None
            state.log_event("self_improve_invalid_change", component=component, reason=why,
                            restored=bool(restored))
            state.log_improvement(
                component=component,
                change=proposal["change_instruction"][:400],
                reason=proposal["reason"][:400],
                result="rejected_invalid",
                weakness=proposal["weakness"][:300],
                invalid_reason=why,
                restored=bool(restored),
            )
            if verbose:
                print(f"НЕВАЛИДНАЯ ПРАВКА ({why}) - откачено")
            continue

        after_score, after_detail = measure(client)
        base_runs = baseline_detail.get("runs") if isinstance(baseline_detail, dict) else None
        after_runs = after_detail.get("runs") if isinstance(after_detail, dict) else None
        noise = max(
            (baseline_detail.get("spread", 0) if isinstance(baseline_detail, dict) else 0),
            (after_detail.get("spread", 0) if isinstance(after_detail, dict) else 0),
        )
        delta = after_score - baseline_score

        # Only call it an improvement if the effect is larger than the benchmark's
        # own run-to-run noise. Otherwise the verdict is honest neutral.
        threshold = max(MIN_EFFECT, noise)
        if delta > threshold:
            verdict = "improved"
        elif delta < -threshold:
            verdict = "regressed"
        else:
            verdict = "neutral"

        rolled_back = False
        if verdict in ("regressed", "neutral") and backup_path:
            # Only a change that beats the noise floor is kept. Keeping neutral
            # edits accumulates arbitrary instructions in skill files: the first
            # successful cycle wrote a section about markdown tools that do not
            # exist in this system and called it an improvement.
            restored = state.restore(backup_path)
            rolled_back = bool(restored)
            state.log_event("self_improve_rollback", component=component, verdict=verdict,
                            backup=backup_path, restored=restored)

        rec = state.log_improvement(
            component=component,
            change=proposal["change_instruction"][:800],
            reason=proposal["reason"][:800],
            result=verdict,
            weakness=proposal["weakness"][:400],
            expected_effect=proposal.get("expected_effect", "")[:300],
            test_plan=proposal.get("test_plan", "")[:300],
            rollback_plan=proposal.get("rollback_plan", "")[:300],
            baseline_score=round(baseline_score, 3),
            after_score=round(after_score, 3),
            delta=round(delta, 3),
            noise_threshold=round(threshold, 3),
            baseline_runs=base_runs,
            after_runs=after_runs,
            rolled_back=rolled_back,
            backup=backup_path,
            detail=(after_detail.get("detail") if isinstance(after_detail, dict) else after_detail)[:6],
        )

        try:
            rag.index_paths([os.path.join(STATE_ROOT, component)])
        except Exception:
            pass

        result["changes"].append(rec)
        baseline_score, baseline_detail = after_score, after_detail
        if verbose:
            print(f"результат: {verdict} "
                  f"({rec['baseline_score']:.2f} -> {rec['after_score']:.2f}, "
                  f"дельта {rec['delta']:+.2f}, порог шума {rec['noise_threshold']:.2f})"
                  f"{'  ОТКАТ' if rolled_back else ''}")

    result["status"] = "completed" if result["changes"] else result["status"]
    result["seconds"] = round(time.time() - started, 1)
    result["final_score"] = baseline_score
    state.log_event("self_improve_end", status=result["status"], seconds=result["seconds"],
                    changes=len(result["changes"]))
    return result


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Цикл самоулучшения ALEX")
    ap.add_argument("changes", nargs="?", type=int, default=1,
                    help="сколько изменений за цикл (по умолчанию 1)")
    ap.add_argument("--thinking", action="store_true",
                    help="показать размышления модели в реальном времени")
    ap.add_argument("--quiet", action="store_true", help="без подробного вывода")
    args = ap.parse_args()

    print(json.dumps(run_cycle(max_changes=args.changes, verbose=not args.quiet,
                               show_thinking=args.thinking), ensure_ascii=False, indent=2))
