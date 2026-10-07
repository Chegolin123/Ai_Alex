"""Did the system actually improve itself? Answer with evidence, not vibes.

Two questions get conflated: "is it running on its own" and "is it getting
better". The first was easy to fake and the second was easy to miss, so this
reports both separately and refuses to call a wash a success.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from runtime import state

HOURLY_LOG = os.path.join(state.ROOT, "logs", "improve-hourly.log")
DAY_SECONDS = 86400

KEPT_RESULTS = {"improved"}
REVERTED_RESULTS = {"regressed", "neutral"}
DEAD_RESULTS = {"rejected_not_applied", "rejected_invalid", "invalidated"}

RUN_RE = re.compile(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\].*код (\d+)")
END_RE = re.compile(r"код (\d+), записано")
STATUS_RE = re.compile(r'"status":\s*"([^"]+)"')


def _parse_ts(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def autonomous_runs():
    """Runs of the scheduled hourly cycle, from its own log.

    Each run ends with a "код N, записано M байт" marker. The cycle status that
    matters is the last one printed inside that block - the earlier ones belong
    to the benchmark cycles measure() runs.
    """
    empty = {"count": 0, "ok": 0, "failed": 0, "last": None, "last_24h": 0,
             "log_exists": False, "statuses": {}}
    if not os.path.exists(HOURLY_LOG):
        return empty

    with open(HOURLY_LOG, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    runs, block = [], []
    for line in lines:
        if END_RE.search(line):
            code = int(END_RE.search(line).group(1))
            ts = None
            m = RUN_RE.search(line)
            if m:
                ts = m.group(1)
            statuses = STATUS_RE.findall("".join(block))
            runs.append({"ts": ts, "code": code,
                         "status": statuses[-1] if statuses else "unknown"})
            block = []
        else:
            block.append(line)

    now = datetime.now(timezone.utc)
    recent = [r for r in runs
              if (t := _parse_ts(r["ts"] or "")) and (now - t).total_seconds() <= DAY_SECONDS]
    counts = {}
    for r in runs:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    return {
        "count": len(runs),
        "ok": sum(1 for r in runs if r["code"] == 0),
        "failed": sum(1 for r in runs if r["code"] != 0),
        "last": runs[-1]["ts"] if runs else None,
        "last_24h": len(recent),
        "log_exists": True,
        "statuses": counts,
        "runs": runs[-8:],
    }


def _is_autonomous(rec):
    """Was this decision made by a scheduled run rather than by hand?"""
    t = _parse_ts((rec.get("timestamp") or "").replace("+00:00", ""))
    if not t:
        return False
    now = datetime.now(timezone.utc)
    age = (now - t).total_seconds()
    if age > DAY_SECONDS:
        return False
    if not os.path.exists(HOURLY_LOG):
        return False
    stamp = t.strftime("%H:%M:%S")
    with open(HOURLY_LOG, "r", encoding="utf-8", errors="replace") as f:
        return any(stamp in line for line in f)


def decisions():
    rows = state.read_improvements(limit=500)
    kept, reverted, dead = [], [], []
    for r in rows:
        res = r.get("result")
        entry = {"ts": (r.get("timestamp") or "")[:19], "component": r.get("component"),
                 "result": res, "delta": r.get("delta"),
                 "autonomous": _is_autonomous(r)}
        (kept if res in KEPT_RESULTS
         else reverted if res in REVERTED_RESULTS
         else dead).append(entry)
    return kept, reverted, dead


def benchmark_trend():
    m = state.load_metrics()
    t = m.get("task_metrics") or {}
    return {"cycles": (m.get("counters") or {}).get("cycles"),
            "success_rate": t.get("success_rate"),
            "avg_attempts_per_stage": t.get("avg_attempts_per_stage"),
            "stage_retry_rate": t.get("stage_retry_rate"),
            "json_reliability": t.get("json_reliability")}


def verdict():
    runs = autonomous_runs()
    kept, reverted, dead = decisions()

    auto_kept = [k for k in kept if k["autonomous"]]
    auto_reverted = [r for r in reverted if r["autonomous"]]
    auto_dead = [d for d in dead if d["autonomous"]]

    statuses = runs.get("statuses", {})
    produced = sum(v for k, v in statuses.items()
                   if k in ("completed", "all_proposals_rejected", "change_failed"))
    starved = sum(v for k, v in statuses.items() if k in ("no_proposal", "unknown"))

    if runs["count"] == 0:
        overall = "НЕ РАБОТАЕТ: цикл ни разу не запускался по расписанию"
    elif auto_kept:
        overall = "УЛУЧШАЕТСЯ: есть подтверждённые улучшения из автономных циклов"
    elif produced and (auto_dead or statuses.get("all_proposals_rejected")):
        overall = ("НЕ УЛУЧШАЕТ: циклы идут и находят дефекты, но все предложения "
                   "отклоняются на стадии применения - система работает вхолостую")
    elif auto_reverted:
        overall = ("НЕ УЛУЧШАЕТ: циклы идут, но всё, что предлагается, оказывается "
                   "нейтральным и откатывается")
    elif produced:
        overall = ("НЕ УЛУЧШАЕТ: циклы работают, предложения появляются, но ни одно "
                   "не сохранено")
    else:
        overall = ("НЕ УЛУЧШАЕТ: циклы запускаются, но ни разу не дошли до "
                   "предложения - модель не смогла разобрать журнал и найти дефект")

    return {
        "verdict": overall,
        "autonomous_runs": runs,
        "cycle_outcomes": {
            "reached_a_proposal": produced,
            "died_before_a_proposal": starved,
        },
        "decisions_last_24h": {
            "kept": len(auto_kept),
            "reverted": len(auto_reverted),
            "rejected": len(auto_dead),
        },
        "all_time_decisions": {"kept": len(kept), "reverted": len(reverted),
                               "rejected": len(dead)},
        "benchmark": benchmark_trend(),
        "kept_details": kept[-5:],
        "rejected_details": dead[-5:],
    }


def render(v):
    OUT = sys.stdout
    OUT.write("=" * 62 + "\n")
    OUT.write("  УЛУЧШАЕТ ЛИ СИСТЕМА САМА СЕБЯ\n")
    OUT.write("=" * 62 + "\n\n")
    r = v["autonomous_runs"]
    OUT.write(f"  ВЕРДИКТ: {v['verdict']}\n\n")
    OUT.write("  Автономные запуски (почасовой цикл)\n")
    if not r["log_exists"]:
        OUT.write("    лога нет\n")
    else:
        OUT.write(f"    всего {r['count']}, успешных {r['ok']}, с ошибкой {r['failed']}\n")
        OUT.write(f"    за сутки {r['last_24h']}, последний {r['last']}\n")
    d = v["decisions_last_24h"]
    OUT.write(f"\n  Решения за сутки без участия человека\n")
    OUT.write(f"    оставлено: {d['kept']}   откачено: {d['reverted']}   отклонено: {d['rejected']}\n")
    co = v["cycle_outcomes"]
    OUT.write(f"    циклов дошло до предложения: {co['reached_a_proposal']}, "
              f"до предложения не довели: {co['died_before_a_proposal']}\n")
    if r.get("statuses"):
        OUT.write(f"    исходы циклов: {r['statuses']}\n")
    b = v["benchmark"]
    OUT.write("\n  Бенчмарк\n")
    OUT.write(f"    циклов {b['cycles']}, успешных {b['success_rate']}, "
              f"попыток на этап {b['avg_attempts_per_stage']}\n")
    OUT.write(f"    надёжность JSON {b['json_reliability']}\n")
    if v["kept_details"]:
        OUT.write("\n  Последние сохранённые улучшения\n")
        for k in v["kept_details"]:
            OUT.write(f"    + {k['ts']} {k['component']} дельта={k['delta']}"
                      f"{' (автономно)' if k['autonomous'] else ' (вручную)'}\n")
    if v["rejected_details"]:
        OUT.write("\n  Последние отклонённые\n")
        for d2 in v["rejected_details"]:
            OUT.write(f"    x {d2['ts']} {d2['component']} -> {d2['result']}\n")
    OUT.write("\n" + "=" * 62 + "\n")
    return 0 if "УЛУЧШАЕТСЯ" in v["verdict"] else 1


if __name__ == "__main__":
    result = verdict()
    if "--json" in sys.argv:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0)
    sys.exit(render(result))
