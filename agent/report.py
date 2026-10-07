"""Human-readable status report for the whole agent system."""

import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent import metrics as metrics_mod
from rag import index as rag
from runtime import llm, state, thermal


def rule(title):
    print(f"\n--- {title} " + "-" * max(4, 56 - len(title)))


def main():
    print("=" * 62)
    print("  АГЕНТНАЯ СИСТЕМА ALEX - Ornith-1.5-9B")
    print("=" * 62)

    rule("LLM")
    client = llm.Llm()
    up = client.health()
    if up:
        ctx = client.context_length()
        print(f"  статус      : работает")
        print(f"  модель      : {client.model}")
        print(f"  контекст    : {ctx}")
    else:
        print(f"  статус      : НЕ РАБОТАЕТ - запусти bin\\start-runtime.ps1")

    gpu = thermal.check()
    if gpu.get("available"):
        bar_len = 30
        frac = min(1.0, (gpu["temp_c"] or 0) / 100)
        bar = "#" * int(bar_len * frac)
        print(f"  GPU         : {gpu['temp_c']}C [{bar}] {gpu['status']}")
        print(f"  VRAM        : {gpu['used_mb']} / {gpu['total_mb']} МБ (свободно {gpu.get('free_mb')})")

    rule("RAG")
    s = rag.stats()
    print(f"  документов  : {s['files']}")
    print(f"  чанков      : {s['chunks']} ({s['db_bytes'] / 1024:.0f} КБ)")

    rule("МЕТРИКИ")
    m = metrics_mod.collect()
    c, t, th = m["counters"], m["task_metrics"], m["thermal_metrics"]
    print(f"  циклов      : {c['cycles']}  (успешных: {t['success_rate']})")
    print(f"  LLM-вызовов : {c['llm_ok']} ok, {c['llm_error']} ошибок")
    print(f"  JSON-репар  : {c['json_parse_failed']} нечитаемых, надёжность {t['json_reliability']}")
    print(f"  tool-call   : {c['tool_call_unknown']} неизвестных")
    print(f"  блокировок  : {c['shell_blocked_by_safety']} опасных команд остановлено")
    print(f"  время цикла : {t['avg_time_per_cycle_sec']} с, LLM-ход {t['avg_llm_turn_sec']} с")
    print(f"  попыток/этап: {t['avg_attempts_per_stage']}")
    print(f"  температура : средняя {th['avg_gpu_temp']}C, максимум {th['max_gpu_temp']}C")
    print(f"  троттлинг   : {th['throttle_events']} событий, пауз {th['thermal_rest_count']}")

    rule("УЛУЧШЕНИЯ")
    hist = state.read_improvements(limit=5)
    if not hist:
        print("  записей пока нет - запусти первый цикл самоулучшения")
    for h in hist:
        mark = {"improved": "+", "regressed": "-", "neutral": "="}.get(h.get("result"), "?")
        print(f"  {mark} {h.get('timestamp', '')[:19]}  {h.get('component')}")
        print(f"      {h.get('weakness', '')[:66]}")

    rule("HERMES")
    hermes_exe = r"C:\Users\finni\AppData\Local\hermes\bin\hermes.exe"
    tools_dir = r"C:\Users\finni\AppData\Local\hermes\tools"
    # Listing the toolchain dirs is unreliable from a non-elevated shell (the
    # repair runs as admin and leaves them unreadable), so check for the actual
    # interpreter the launcher hardcodes.
    hermes_python = os.path.join(tools_dir, "python-3.14.7+20260901-win32-x64", "python.exe")
    has_toolchain = os.path.exists(hermes_python)
    print(f"  установлен  : {os.path.exists(hermes_exe)}")
    print(f"  конфиг      : C:\\Users\\finni\\AppData\\Local\\hermes\\config.yaml")
    print(f"  доступ      : approvals.mode=off (полный доступ к файлам и командам)")
    if has_toolchain:
        print("  версия      : 0.21.5+8488.gee320cb, Python 3.14.7")
        print("  тулчейн     : на месте и доступен")
    else:
        print("  тулчейн     : СЛОМАН - не виден интерпретатор, hermes.exe падает с кодом 1")
        print("                починить: powershell -File C:\\Users\\finni\\agent-system\\bin\\repair-hermes.ps1")

    print("\n" + "=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
