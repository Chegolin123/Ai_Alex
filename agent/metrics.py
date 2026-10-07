"""Collect metrics from the event log into state/metrics.json."""

import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rag import index as rag
from runtime import state, thermal


def collect(window_minutes=1440):
    events = state.read_events(since_minutes=window_minutes, limit=5000)
    if not events:
        events = state.read_events(limit=5000)

    cycles = [e for e in events if e.get("kind") == "cycle_end"]
    parse_fail = [e for e in events if e.get("kind") == "json_parse_failed"]
    llm_ok = [e for e in events if e.get("kind") == "llm_ok"]
    llm_err = [e for e in events if e.get("kind") == "llm_error"]
    unknown_tools = [e for e in events if e.get("kind") == "tool_call_unknown"]
    shell = [e for e in events if e.get("kind") == "shell_exec"]
    blocked = [e for e in shell if e.get("blocked")]
    throttled = [e for e in events if (e.get("temp_c") or 0) >= thermal.THRESHOLDS["hot"]]
    rested = [e for e in events if (e.get("waited_sec") or 0) > 0]
    temps = [e["temp_c"] for e in events if isinstance(e.get("temp_c"), (int, float))]

    cycle_times = [e.get("seconds") for e in cycles if isinstance(e.get("seconds"), (int, float))]
    llm_times = [e.get("seconds") for e in llm_ok if isinstance(e.get("seconds"), (int, float))]

    completed = sum(1 for c in cycles if c.get("status") == "completed")
    stage_attempts = []
    for e in events:
        for s in (e.get("stage_attempts") or []):
            if isinstance(s, dict) and isinstance(s.get("attempts"), int):
                stage_attempts.append(s["attempts"])
    retried = sum(1 for a in stage_attempts if a > 1)

    metrics = {
        "updated_at": state.now(),
        "window_minutes": window_minutes,
        "counters": {
            "cycles": len(cycles),
            "llm_ok": len(llm_ok),
            "llm_error": len(llm_err),
            "json_parse_failed": len(parse_fail),
            "tool_call_unknown": len(unknown_tools),
            "shell_exec": len(shell),
            "shell_blocked_by_safety": len(blocked),
        },
        "task_metrics": {
            "success_rate": round(completed / len(cycles), 3) if cycles else None,
            "avg_attempts_per_stage": round(sum(stage_attempts) / len(stage_attempts), 2) if stage_attempts else None,
            "stages_seen": len(stage_attempts),
            "stage_retry_rate": round(retried / len(stage_attempts), 3) if stage_attempts else None,
            "avg_time_per_cycle_sec": round(sum(cycle_times) / len(cycle_times), 1) if cycle_times else None,
            "avg_llm_turn_sec": round(sum(llm_times) / len(llm_times), 1) if llm_times else None,
            "json_reliability": round(len(llm_ok) / (len(llm_ok) + len(parse_fail)), 3)
            if (llm_ok or parse_fail) else None,
        },
        "thermal_metrics": {
            "avg_gpu_temp": round(sum(temps) / len(temps), 1) if temps else None,
            "max_gpu_temp": max(temps) if temps else None,
            "throttle_events": len(throttled),
            "thermal_rest_count": len(rested),
            "current": thermal.check(),
        },
        "rag": rag.stats(),
    }
    previous = state.load_metrics()
    metrics["previous_updated_at"] = previous.get("updated_at")
    return state.merge_metrics(metrics)


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False, indent=2))
