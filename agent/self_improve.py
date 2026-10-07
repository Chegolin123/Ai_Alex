"""Self-improvement cycle: notice a defect, change exactly one thing, measure, roll back if worse.

This is the system's first assigned task. It reads its own event log and metrics,
asks the model to name the weakest link, applies one change, tests it on 1-3 real
tasks, compares against the pre-change baseline, and keeps or reverts. Every
outcome is written to improvements.jsonl regardless of result.
"""

import json
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent import loop, tools
from agent import questions as backlog
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

    Measured reason the edit no longer goes through the model's tool calling: the
    sub-agent kept exploring - file_read, file_read, shell_exec, shell_exec - and
    never called file_write, so every autonomous cycle died with "sub-agent made
    no successful file_write". The same two-turn shape that fixed the reasoning
    problem applies here: read the file in code, let the model return only the new
    text with no tools available, then write it here. The unreliable step is
    removed rather than prompted more firmly.
    """
    component = proposal["component"].replace("\\", "/").strip()
    if component not in KNOWN_COMPONENTS:
        return False, {"error": f"component not on allowlist: {component}"}

    inline = extract_file_content(component, proposal.get("change_instruction") or "")
    if inline is not None:
        result = tools.file_write(component, inline)
        return bool(result.get("ok")), result

    path = os.path.join(STATE_ROOT, component)
    try:
        with open(path, "r", encoding="utf-8") as f:
            original = f.read()
    except OSError as e:
        return False, {"error": f"не читается {component}: {e}"}

    instruction = (
        f"Ниже ПОЛНОЕ текущее содержимое файла {component}.\n"
        f"Верни ПОЛНОЕ новое содержимое этого же файла с одной правкой.\n\n"
        f"Что исправляем: {proposal['weakness']}\n"
        f"Инструкция: {proposal['change_instruction']}\n"
        f"Ожидаемый эффект: {proposal.get('expected_effect', '')}\n\n"
        f"Правила: внеси только эту правку, остальное сохрани дословно. "
        f"Не сокращай, не пиши «...» вместо кода, не добавляй пояснений. "
        f"Верни только текст файла.\n\n"
        f"===== НАЧАЛО ФАЙЛА =====\n{original}\n===== КОНЕЦ ФАЙЛА ====="
    )

    messages = [
        {"role": "system", "content":
            "Ты редактор файлов. Возвращаешь полное новое содержимое файла "
            "и ничего больше: без пояснений, без markdown-обёрток, без нумерации строк."},
        {"role": "user", "content": instruction},
    ]

    # Reasoning off, no tools: the model has one job and nothing to explore.
    # Budget has to fit the WHOLE file plus slack. Measured failure: at 4000
    # tokens the reply was cut at 54% of runtime/llm.py, which sailed past the old
    # "half the original length" check and produced an unimportable module.
    budget = max(8000, int(len(original) * 0.9))
    try:
        res = client.complete(
            messages,
            max_tokens=budget,
            temperature=0.2,
            reasoning_effort="none",
        )
    except llm.LlmError as e:
        state.log_event("self_improve_rejected_proposal", component=component,
                        reason=f"ошибка правки: {str(e)[:120]}")
        return False, {"error": f"ошибка правки: {str(e)[:200]}"}

    new_text = _clean_editor_output(res.get("content") or "")
    if not new_text.strip():
        state.log_event("self_improve_rejected_proposal", component=component,
                        reason="модель вернула пустой текст файла")
        return False, {"error": "модель вернула пустой текст файла"}

    # An edit must keep the file recognisable. Truncation is the failure mode that
    # actually happened, and it is silent, so it needs a hard ratio, not a loose one.
    before_len = len(original)
    before_lines = original.count("\n")
    after_len = len(new_text)
    after_lines = new_text.count("\n")
    if after_len < before_len * 0.85:
        state.log_event("self_improve_rejected_proposal", component=component,
                        reason=f"ответ похож на обрезанный: {after_len} против {before_len} символов")
        return False, {"error": f"ответ обрезан: {after_len} символов против {before_len} в оригинале"}
    if after_lines < before_lines * 0.7:
        state.log_event("self_improve_rejected_proposal", component=component,
                        reason=f"потеряны строки: {after_lines} против {before_lines}")
        return False, {"error": f"потеряны строки: {after_lines} против {before_lines}"}

    result = tools.file_write(component, new_text)
    ok = bool(result.get("ok"))
    state.log_event("self_improve_change_applied", component=component, ok=ok,
                    chars_before=before_len, chars_after=after_len,
                    lines_before=before_lines, lines_after=after_lines)
    return ok, result


def _clean_editor_output(text):
    """Strip markdown fences and any prose around the file body.

    The model wraps the rewritten file in a code fence. The old version looked
    for the fence regex on the wrong module (loop._FENCE, which does not exist -
    it lives in runtime.llm), so the fence was never stripped and the trailing
    ``` landed inside the written file, making it unparseable every single run.
    """
    s = text.strip()

    if s.startswith("```"):
        s = re.sub(r"^```[ \t]*[\w+-]*[ \t]*\r?\n?", "", s, count=1)
        s = re.sub(r"\r?\n?```[ \t]*$", "", s, count=1)

    start_marker = "===== НАЧАЛО ФАЙЛА ====="
    if start_marker in s:
        s = s.split(start_marker, 1)[1]
    end_marker = "===== КОНЕЦ ФАЙЛА ====="
    if end_marker in s:
        s = s.split(end_marker, 1)[0]

    s = s.strip()

    if "```" in s and s.count("```") == 2:
        head, _, rest = s.partition("```")
        if not head.strip():
            s = rest.rsplit("```", 1)[0]

    return s.strip() + "\n"


def _park_raw(question, context="", priority="normal", options=None):
    try:
        res = backlog.ask(question, source="self-improvement", context=context,
                          options=options or [], priority=priority)
        loop.note("вопрос человеку: %s (%s)" % (question[:80], res.get("id")))
        return res
    except Exception as e:
        state.log_event("question_park_failed", error=str(e)[:200])
        return None


def _park_question(proposal, error):
    """A proposal the executor could not carry out is usually a decision a human
    has to make, so it becomes a question instead of a silent dead end."""
    try:
        opts = []
        options = proposal.get("options")
        if isinstance(options, list):
            opts = [str(o) for o in options][:4]
        if not opts:
            opts = [
                "разрешить правку этого компонента",
                "запретить правку этого компонента",
                "перенести правку на другой файл",
            ]
        return _park_raw(
            "Меняем %s, но применить правку не вышло: %s. Разрешаешь?" % (
                proposal.get("component"), error[:160]),
            context="Дефект: %s\nИнструкция: %s" % (
                (proposal.get("weakness") or "")[:400],
                (proposal.get("change_instruction") or "")[:400]),
            priority="high",
            options=opts,
        )
    except Exception:
        return None


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

    loop.note("цикл самоулучшения: считаю базовый уровень, 3 прогона бенчмарка")
    baseline_score, baseline_detail = measure(client)
    if verbose:
        print(f"baseline: {baseline_score:.2f}  прогоны: {baseline_detail.get('runs')}  "
              f"разброс: {baseline_detail.get('spread')}")
    state.log_event("self_improve_baseline", score=baseline_score, detail=baseline_detail)

    for _ in range(max_changes):
        loop.note("базовый уровень: %.2f, ищу слабое место" % baseline_score)
        proposal = propose(client, evidence, benchmark=baseline_detail, show_thinking=show_thinking)
        if not proposal:
            result["status"] = "no_proposal"
            _park_raw(
                "Не удалось определить слабое место автоматически: модель не смогла "
                "разобрать журнал и назвать конкретный дефект. Нужен взгляд человека: "
                "что в системе сейчас выглядит неправильным?",
                context="Счётчики: %s. Бенчмарк: %s" % (
                    json.dumps(evidence["event_counts"], ensure_ascii=False)[:400],
                    json.dumps(baseline_detail.get("runs") if isinstance(baseline_detail, dict) else None,
                               ensure_ascii=False)),
            )
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
            # A rejected proposal is still work done: the model found a defect and
            # named a fix, the executor just could not carry it out. Logging it
            # matters - the ledger is what the next cycle reads to avoid repeating
            # the same dead end.
            state.log_improvement(
                component=component,
                change=proposal["change_instruction"][:400],
                reason=proposal["reason"][:400],
                result="rejected_not_applied",
                weakness=proposal["weakness"][:300],
                reject_reason=str(info.get("error"))[:300],
            )
            result["rejected"] = result.get("rejected", 0) + 1
            loop.note("предложение отклонено на стадии правки: %s" % str(info.get("error"))[:120])
            _park_question(proposal, str(info.get("error")))
            if verbose:
                print(f"ОТКЛОНЕНО: {info.get('error')}")
            continue

        loop.note("правка применена к %s, проверяю и меряю заново" % component)
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
            if not restored:
                # rollback itself failed - this is the dangerous case, because the
                # tree may be left holding a change that does not compile
                _park_raw(
                    "Правка %s сделала файл нерабочим и НЕ была откатана: %s. "
                    "Нужен человек." % (component, why[:160]),
                    context="Инструкция: %s" % (proposal.get("change_instruction") or "")[:400],
                    priority="high",
                    options=["откатить вручную", "починить самому", "оставить как есть"],
                )
            if verbose:
                print(f"НЕВАЛИДНАЯ ПРАВКА ({why}) - откачено"
                      f"{'' if restored else ', ОТКАТ НЕ УДАЛСЯ'}")
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

        loop.note("вердикт: %s (%.2f -> %.2f)" % (verdict, baseline_score, after_score))
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

    result["status"] = "completed" if result["changes"] else (
        "all_proposals_rejected" if result.get("rejected") else result["status"])
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
