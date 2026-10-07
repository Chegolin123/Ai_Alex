"""Agent loop: plan -> sub-agent -> verify, with retry and escalation limits.

The loop drives the local Ornith runtime. Every turn passes the thermal gate,
every model reply is parsed with the repair parser (the model does not honour
JSON-only contracts), and every step is logged so the self-improvement cycle
has something to read.
"""

import json
import os
import sys
import time

from agent import schemas
from agent import verify as verify_mod
from agent import tools
from runtime import llm, state, thermal

SKILLS_DIR = os.path.join(state.ROOT, "skills")

LIMITS = {
    "attempts_per_stage": 3,
    "same_error_escalate": 2,
    "steps_per_cycle": 20,
}

MAX_TOOL_CHARS = 3000


def load_skill(name):
    path = os.path.join(SKILLS_DIR, f"{name}.md")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def ask_json(client, skill_name, prompt, max_tokens=1200, temperature=0.3, tools_enabled=False,
             max_tool_rounds=4):
    """One model turn that must come back as JSON.

    Two measured constraints apply here:
      - the reply is grammar-constrained by the skill's JSON schema, because the
        model otherwise narrates instead of answering
      - reasoning is switched off for these turns, because a reasoning model
        spends the whole max_tokens budget thinking and returns empty content
        with finish_reason=length
    """
    system = load_skill(skill_name)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    gate, snap, waited = thermal.gate(skill_name)
    if not gate:
        return None, {"error": "thermal gate refused", "gpu": snap}

    t0 = time.time()
    schema = schemas.for_skill(skill_name)

    if not tools_enabled:
        kw = {"max_tokens": max_tokens, "temperature": temperature, "reasoning_effort": "none"}
        if schema:
            kw["json_schema"] = schema
            kw["schema_name"] = skill_name
        try:
            res = client.complete(messages, **kw)
        except llm.LlmError as e:
            state.log_event("llm_error", skill=skill_name, error=str(e)[:300])
            return None, {"error": str(e)}
        return _finish(res, skill_name, snap, waited, max_tokens, schema_enforced=True)

    # Agentic phase: keep serving tool calls until the model stops asking for them.
    # One round was not enough - the sub-agent read the target file and then had no
    # way to write it back, so every edit silently degraded to a read.
    res = None
    results = []
    for round_no in range(max_tool_rounds):
        try:
            res = client.complete(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort="none",
                tools=tools.as_tools(),
            )
        except llm.LlmError as e:
            state.log_event("llm_error", skill=skill_name, round=round_no, error=str(e)[:300])
            return None, {"error": str(e), "tool_results": results}

        calls = res.get("tool_calls") or []
        if not calls:
            break

        state.log_event("llm_tool_calls", skill=skill_name, round=round_no,
                        names=[c.get("name") for c in calls], temp_c=snap.get("temp_c"))
        messages.append({"role": "assistant", "content": res.get("content") or ""})
        for call in calls:
            out = tools.dispatch(call)
            results.append({"name": call.get("name"), "ok": out.get("ok"), "result": out})
            messages.append({
                "role": "tool",
                "name": call.get("name") or "tool",
                "tool_call_id": call.get("id") or f"call_{len(results)}",
                "content": json.dumps(out, ensure_ascii=False)[:MAX_TOOL_CHARS],
            })

    if res is None:
        return None, {"error": "no model response"}

    # Structured phase: the agentic loop is over, force the JSON contract.
    kw2 = {"max_tokens": max_tokens, "temperature": temperature, "reasoning_effort": "none"}
    if schema:
        kw2["json_schema"] = schema
        kw2["schema_name"] = skill_name
    try:
        final = client.complete(messages, **kw2)
    except llm.LlmError as e:
        return None, {"error": f"final structured turn failed: {e}", "tool_results": results}

    parsed, meta = _finish(final, skill_name, snap, waited, max_tokens, schema_enforced=True)
    if meta is not None:
        meta["tool_results"] = results
    return parsed, meta if meta is not None else {"error": "final turn produced no verdict",
                                                 "tool_results": results}


def _finish(res, skill_name, snap, waited, max_tokens, schema_enforced):
    """Turn one model reply into (parsed, meta), logging honestly."""
    parsed = llm.repair_json(res["content"])
    dt = round(res.get("seconds") or 0, 1)
    meta = {
        "skill": skill_name,
        "seconds": dt,
        "reasoning_chars": len(res.get("reasoning") or ""),
        "tool_calls": res.get("tool_calls") or [],
        "temp_c": snap.get("temp_c"),
        "waited_sec": waited,
        "usage": res.get("usage"),
        "schema_enforced": schema_enforced,
        "finish_reason": res.get("finish_reason"),
    }
    if parsed is None:
        truncated = res.get("finish_reason") == "length"
        state.log_event(
            "output_truncated" if truncated else "json_parse_failed",
            skill=skill_name,
            preview=(res.get("content") or "")[:300],
            finish_reason=res.get("finish_reason"),
            schema_enforced=schema_enforced,
            max_tokens=max_tokens,
        )
        meta["parse_failed"] = True
        meta["truncated"] = truncated
        return None, meta
    state.log_event("llm_ok", skill=skill_name, seconds=dt,
                    reasoning_chars=meta["reasoning_chars"],
                    schema_enforced=schema_enforced, temp_c=snap.get("temp_c"))
    return parsed, meta


def run_cycle(goal, client=None, verbose=True):
    client = client or llm.Llm()
    started = time.time()
    cycle = {"goal": goal, "started": state.now(), "stages": [], "steps": 0, "status": "unknown"}
    state.log_event("cycle_start", goal=goal)

    if not client.health():
        cycle["status"] = "no_runtime"
        state.log_event("cycle_abort", reason="llama-server unreachable")
        if verbose:
            print("LLM недоступен. Запусти: powershell -File bin\\start-runtime.ps1")
        return cycle

    plan, meta = ask_json(client, "plan", f"Цель: {goal}")
    if not plan or not plan.get("stages"):
        cycle["status"] = "plan_failed"
        state.log_event("cycle_abort", reason="plan failed", meta=meta)
        if verbose:
            print("Plan Agent не вернул этапы. Подробности в events.jsonl")
        return cycle

    if verbose:
        print(f"план: {len(plan['stages'])} этап(ов)")
    state.log_event("plan_ready", stages=[s.get("id") for s in plan["stages"]])

    history = []
    for stage in plan["stages"]:
        sid = stage.get("id") or "stage"
        criteria = stage.get("acceptance_criteria") or "этап выполнен"
        budget = min(int(stage.get("budget_steps") or 5), LIMITS["steps_per_cycle"])

        entry = {"id": sid, "goal": stage.get("goal"), "attempts": 0, "verified": False}
        for attempt in range(1, LIMITS["attempts_per_stage"] + 1):
            entry["attempts"] = attempt
            cycle["steps"] += 1
            if cycle["steps"] > LIMITS["steps_per_cycle"]:
                entry["status"] = "step_budget_exceeded"
                break

            loop_mod = sys.modules[__name__]
            last_error = history[-1]["error"] if history else ""
            correction = (
                f"\n\nВАЖНО: в прошлой попытке было сказано, что файл создан, но инструмент "
                f"file_write фактически не вызывался. Либо вызови file_write сейчас, либо верни "
                f"status=blocked с честной причиной." if history and "file_write" in last_error else ""
            )
            sub, sub_meta = ask_json(
                client, "sub_agent",
                f"Задача: {stage.get('goal')}\nКритерий: {criteria}{correction}\n"
                f"Контекст предыдущих попыток: {json.dumps(history[-3:], ensure_ascii=False)}",
                max_tokens=1100, tools_enabled=True,
            )
            if not sub:
                entry["status"] = "sub_agent_failed"
                history.append({"stage": sid, "error": "sub-agent вернул нечитаемый ответ"})
                continue

            entry["summary"] = sub.get("summary")
            entry["files_changed"] = sub.get("files_changed") or []
            entry["artifacts"] = sub.get("artifacts") or []

            dispatched = [r.get("name") for r in (sub_meta.get("tool_results") or [])]
            writes = [r for r in (sub_meta.get("tool_results") or [])
                      if r.get("name") == "file_write" and (r.get("result") or {}).get("ok")]
            entry["tools_used"] = dispatched

            declared = entry["files_changed"] + entry["artifacts"]
            if declared and not writes:
                entry["status"] = "unbacked_claim"
                entry["verify_method"] = "tool_audit"
                entry["evidence"] = (
                    f"объявлены файлы {declared}, но ни один вызов file_write не выполнен "
                    f"(инструменты: {dispatched or 'нет'})"
                )
                history.append({"stage": sid, "attempt": attempt, "error": entry["evidence"],
                                "severity": "high"})
                continue

            det_passed, checks, applicable = verify_mod.deterministic(criteria, entry)
            entry["deterministic"] = {"applicable": applicable, "checks": checks}

            if applicable:
                if det_passed:
                    entry["verified"] = True
                    entry["status"] = "verified"
                    entry["evidence"] = f"детерминированно: {len(checks)} проверок пройдено"
                    entry["verify_method"] = "deterministic"
                    break
                failed = [c for c in checks if not c["passed"]]
                entry["status"] = "verify_failed"
                entry["verify_method"] = "deterministic"
                entry["evidence"] = f"детерминированно провалено: {json.dumps(failed, ensure_ascii=False)[:300]}"
                verdict = {"passed": False, "detail": entry["evidence"],
                           "severity": "high" if any(c.get("missing") for c in failed) else "medium"}
                meta = {"method": "deterministic"}
            else:
                verdict, meta = verify_mod.judge_with_model(client, loop_mod, criteria, entry, checks)
                entry["verify_method"] = (meta or {}).get("method") or "llm"

            if verdict and verdict.get("passed"):
                entry["verified"] = True
                entry["status"] = "verified"
                entry["evidence"] = verdict.get("evidence")
                break

            entry["status"] = "verify_failed"
            history.append({
                "stage": sid,
                "attempt": attempt,
                "error": (verdict or {}).get("detail") or (verdict or {}).get("evidence") or "verify не прошёл",
                "severity": (verdict or {}).get("severity"),
            })

            troubles, _ = ask_json(
                client, "plan_trouble",
                f"Ошибки: {json.dumps(history[-3:], ensure_ascii=False)}",
                max_tokens=700,
            )
            if troubles and troubles.get("escalate"):
                entry["status"] = "escalated"
                break

        cycle["stages"].append(entry)

    done = all(s.get("verified") for s in cycle["stages"]) and cycle["stages"]
    cycle["status"] = "completed" if done else "incomplete"
    cycle["seconds"] = round(time.time() - started, 1)
    cycle["finished"] = state.now()
    cycle["stage_attempts"] = [
        {"id": s.get("id"), "attempts": s.get("attempts"), "verified": s.get("verified"),
         "status": s.get("status")}
        for s in cycle["stages"]
    ]
    state.log_event("cycle_end", goal=goal, status=cycle["status"],
                    seconds=cycle["seconds"], steps=cycle["steps"],
                    stage_attempts=cycle["stage_attempts"])
    if verbose:
        for s in cycle["stages"]:
            mark = "OK " if s.get("verified") else "FAIL"
            print(f"  [{mark}] {s['id']}: {s.get('status')} ({s.get('attempts')} попыток)")
        print(f"итог: {cycle['status']} за {cycle['seconds']}с")
    return cycle
