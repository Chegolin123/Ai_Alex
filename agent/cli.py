"""Command-line surface of the ALEX system, for the real Hermes CLI.

Hermes orchestrates. This module is the tool layer underneath it: every special
capability the machine has (thermal gate, destructive-command guard, RAG, agent
cycle, self-improvement, versioning) is one command that Hermes runs through its
own `terminal` tool. Nothing here is a parallel orchestrator - it is plumbing.

`alex-guard` and `alex-run` exist so that the safety rules apply to Hermes too,
not just to the Python loop: the skills route every shell command through them.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rag import index as rag
from runtime import safety, state, thermal

OUT = sys.stdout


def emit(obj, as_json=True):
    if as_json:
        OUT.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    return 0


# --- guard / run ----------------------------------------------------------

def cmd_guard(a):
    allowed, reason = safety.classify(a.cmd)
    OUT.write(json.dumps({"cmd": a.cmd, "allowed": allowed, "reason": reason},
                         ensure_ascii=False) + "\n")
    return 0 if allowed else 1


def cmd_run(a):
    """Gate first, then execute. The gate is not optional and not bypassable."""
    cmd = a.cmd if isinstance(a.cmd, str) else " ".join(a.cmd)
    allowed, reason = safety.classify(cmd)
    if not allowed:
        OUT.write(f"ЗАБЛОКИРОВАНО: {reason}\nкоманда: {cmd}\n")
        state.log_event("hermes_cmd_blocked", cmd=cmd[:300], reason=reason)
        return 3

    gate, snap, waited = thermal.gate("hermes_run")
    if not gate:
        OUT.write(f"ТЕРМОЗАЩИТА: {snap.get('temp_c')}C, цикл отменён до остывания\n")
        state.log_event("hermes_cmd_thermal_refused", cmd=cmd[:200], temp_c=snap.get("temp_c"))
        return 4

    result = safety.run(cmd, cwd=a.cwd or state.ROOT, timeout=a.timeout)
    if result.get("stdout"):
        OUT.write(result["stdout"].rstrip() + "\n")
    if result.get("stderr"):
        OUT.write("[stderr]\n" + result["stderr"].rstrip() + "\n")
    OUT.write(f"\n[exit={result.get('returncode')}]\n")
    state.log_event("hermes_cmd_run", cmd=cmd[:300],
                    returncode=result.get("returncode"), temp_c=snap.get("temp_c"))
    return int(result.get("returncode") or 0)


# --- machine state --------------------------------------------------------

def cmd_thermal(a):
    s = thermal.check()
    state.log_event("alex_thermal", temp_c=s.get("temp_c"), status=s.get("status"),
                    used_mb=s.get("used_mb"))
    if a.json:
        return emit(s)
    if not s.get("available"):
        OUT.write("nvidia-smi недоступен\n")
        return 0
    frac = min(1.0, (s.get("temp_c") or 0) / 100)
    bar = "#" * int(30 * frac)
    OUT.write(f"GPU {s['temp_c']}C [{bar}] {s['status']}\n")
    OUT.write(f"VRAM {s['used_mb']} / {s['total_mb']} МБ (свободно {s.get('free_mb')})\n")
    OUT.write(f"нагрузка {s.get('util_pct')}%  питание {s.get('power_w')} Вт\n")
    OUT.write(f"пороги: тёпло {thermal.THRESHOLDS['warm']} / горячо "
              f"{thermal.THRESHOLDS['hot']} / критично {thermal.THRESHOLDS['critical']}\n")
    return 0


def cmd_gate(a):
    """Exit 1 when the machine is too hot to keep working. Use as a pre-flight."""
    s = thermal.check()
    hot = s.get("temp_c") is not None and s["temp_c"] >= thermal.THRESHOLDS["hot"]
    state.log_event("alex_gate", temp_c=s.get("temp_c"), status=s.get("status"), hot=hot)
    if not a.json:
        OUT.write(f"{s.get('status')} {s.get('temp_c')}C\n")
    else:
        emit(s)
    return 1 if hot else 0


def cmd_rag(a):
    query = a.query if isinstance(a.query, str) else " ".join(a.query)
    hits = rag.search(query, limit=a.limit)
    state.log_event("alex_rag", query=query[:150], hits=len(hits))
    if a.json:
        return emit(hits)
    if not hits:
        OUT.write("ничего не найдено\n")
        return 0
    for h in hits:
        OUT.write(f"\n--- {h['path']} (чанк {h['chunk']}, score {h['score']})\n")
        OUT.write(h["snippet"].replace("\n", " ") + "\n")
    return 0


def cmd_index(a):
    res = rag.index_paths(verbose=a.verbose)
    state.log_event("alex_index", **{k: v for k, v in res.items()})
    return emit(res)


def cmd_report(a):
    from agent import report
    return report.main()


# --- cycles ---------------------------------------------------------------

def cmd_cycle(a):
    from agent.loop import run_cycle
    from runtime import llm
    cycle = run_cycle(a.goal, client=llm.Llm(), verbose=True)
    if a.json:
        emit(cycle)
    OUT.write(f"\nИТОГ: {cycle['status']} за {cycle.get('seconds')}с, шагов {cycle['steps']}\n")
    for s in cycle["stages"]:
        mark = "OK" if s.get("verified") else "FAIL"
        OUT.write(f"  [{mark}] {s.get('id')}: {s.get('status')} "
                  f"({s.get('attempts')} попыток, {s.get('verify_method', '-')})\n")
    return 0 if cycle["status"] == "completed" else 1


def cmd_improve(a):
    from agent.self_improve import run_cycle
    from runtime import llm
    res = run_cycle(client=llm.Llm(), max_changes=a.changes, verbose=True,
                    show_thinking=a.thinking)
    if a.json:
        emit(res)
    for c in res["changes"]:
        OUT.write(f"\n{c['result'].upper()}: {c['component']} "
                  f"({c.get('baseline_score')} -> {c.get('after_score')})"
                  f"{'  ОТКАТ' if c.get('rolled_back') else ''}\n")
        OUT.write(f"  дефект: {c.get('weakness', '')[:200]}\n")
    return 0


def cmd_improvements(a):
    rows = state.read_improvements(limit=a.limit)
    if a.json:
        return emit(rows)
    if not rows:
        OUT.write("записей об улучшениях пока нет\n")
        return 0
    for r in rows:
        mark = {"improved": "+", "regressed": "!", "neutral": "=",
                "rejected_invalid": "x"}.get(r.get("result"), "?")
        OUT.write(f"{mark} {r.get('timestamp', '')[:19]}  {r.get('component')}  "
                  f"дельта={r.get('delta')}\n    {str(r.get('weakness', ''))[:150]}\n")
    return 0


def cmd_version(a):
    sys.argv = ["version_agent", "snapshot" if a.snapshot else "status"]
    if a.snapshot:
        sys.argv += ["--bump", a.bump, "--push"]
    from agent import version_agent
    return version_agent.main()


def cmd_state(a):
    return emit({
        "llm": state.load_metrics(),
        "thermal": thermal.check(),
        "improvements": len(state.read_improvements(limit=500)),
        "rag": rag.stats(),
        "recent_events": state.read_events(since_minutes=a.minutes, limit=a.limit),
    })


def cmd_watch(a):
    """Follow the live transcript of a thinking run.

    Hermes captures terminal output only when the command returns, and truncates
    it, so ongoing work is not visible in its window. This tails the file the
    streaming path writes instead.
    """
    path = a.file or os.path.join(state.ROOT, "logs", "improve-thinking.log")
    if not os.path.exists(path):
        OUT.write(f"транскрипта нет: {path}\n"
                  f"запусти цикл с --thinking, чтобы он появился\n")
        return 1

    OUT.write(f"слежу за {path}, Ctrl+C - остановить\n\n")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, os.SEEK_END)
        deadline = time.time() + (a.seconds or 0)
        while True:
            chunk = f.readline()
            if chunk:
                OUT.write(chunk)
                OUT.flush()
                continue
            if a.follow and time.time() > deadline:
                OUT.write("\nвремя слежения истекло\n")
                return 0
            if not a.follow and time.time() > deadline:
                return 0
            time.sleep(0.4)


def cmd_status_live(a):
    """One-line progress of a possibly-running improvement cycle."""
    rows = state.read_events(since_minutes=a.minutes, limit=500)
    last_start = None
    last_end = None
    for r in rows:
        if r["kind"] == "self_improve_start":
            last_start = r
        if r["kind"] == "self_improve_end":
            last_end = r
    log_path = os.path.join(state.ROOT, "logs", "improve-thinking.log")
    OUT.write(json.dumps({
        "running": last_start is not None and (last_end is None or
                                                last_end["ts"] < last_start["ts"]),
        "last_start": (last_start or {}).get("ts"),
        "last_end": (last_end or {}).get("ts"),
        "last_result": (last_end or {}).get("status"),
        "last_seconds": (last_end or {}).get("seconds"),
        "transcript_exists": os.path.exists(log_path),
        "transcript_bytes": os.path.getsize(log_path) if os.path.exists(log_path) else 0,
    }, ensure_ascii=False, indent=2) + "\n")
    return 0


def cmd_progress(a):
    """Honest answer to "is it actually getting better on its own"."""
    from agent import progress
    result = progress.verdict()
    if a.json:
        emit(result)
        return 0 if "УЛУЧШАЕТСЯ" in result["verdict"] else 1
    return progress.render(result)


def main():
    ap = argparse.ArgumentParser(prog="alex", description="Система ALEX - слой команд для Hermes")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("guard", help="проверить команду против защиты (0 - можно, 1 - нельзя)")
    p.add_argument("cmd")
    p.set_defaults(fn=cmd_guard)

    p = sub.add_parser("run", help="проверить и выполнить команду")
    p.add_argument("cmd", nargs="+")
    p.add_argument("--cwd")
    p.add_argument("--timeout", type=int)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("thermal", help="температура GPU и VRAM")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_thermal)

    p = sub.add_parser("gate", help="1, если слишком горячо")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_gate)

    p = sub.add_parser("rag", help="поиск по файлам системы")
    p.add_argument("query", nargs="+")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_rag)

    p = sub.add_parser("index", help="переиндексировать RAG")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(fn=cmd_index)

    p = sub.add_parser("report", help="сводка состояния системы")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("cycle", help="прогнать задачу через цикл агента")
    p.add_argument("goal")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_cycle)

    p = sub.add_parser("improve", help="цикл самоулучшения")
    p.add_argument("changes", nargs="?", type=int, default=1)
    p.add_argument("--thinking", action="store_true", help="показать размышления модели")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_improve)

    p = sub.add_parser("improvements", help="история улучшений")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_improvements)

    p = sub.add_parser("version", help="состояние версии или снимок с отправкой")
    p.add_argument("--snapshot", action="store_true")
    p.add_argument("--bump", choices=["major", "minor", "patch"], default="patch")
    p.set_defaults(fn=cmd_version)

    p = sub.add_parser("state", help="сводка состояния в JSON")
    p.add_argument("--minutes", type=int, default=60)
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(fn=cmd_state)

    p = sub.add_parser("watch", help="следить за живым транскриптом размышлений")
    p.add_argument("--file", help="свой файл вместо logs/improve-thinking.log")
    p.add_argument("--seconds", type=int, default=0, help="сколько секунд следить (0 - до Ctrl+C)")
    p.add_argument("--follow", action="store_true", help="следить, пока файл растёт")
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("live", help="идёт ли сейчас цикл самоулучшения")
    p.add_argument("--minutes", type=int, default=180)
    p.set_defaults(fn=cmd_status_live)

    p = sub.add_parser("progress", help="улучшает ли система сама себя: честный вердикт")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_progress)

    a = ap.parse_args()
    try:
        return a.fn(a)
    except Exception as e:
        sys.stderr.write(f"alex: {type(e).__name__}: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
