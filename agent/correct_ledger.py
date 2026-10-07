"""Append an honest correction to the improvement ledger.

The ledger is append-only on purpose. Rewriting a past entry would hide the fact
that the system once recorded a false improvement; adding a correction keeps the
error visible and stops future cycles from trusting it.
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from runtime import state

rec = {
    "timestamp": state.now(),
    "component": "skills/sub_agent.md",
    "result": "invalidated",
    "change": "ПОПРАВКА записи от 2026-10-06T23:19:58 - та правка была отменена вручную",
    "reason": (
        "Запись утверждала result=improved (0.5 -> 1.0). Но правка добавляла раздел "
        "«Markdown-инструменты» про инструменты, которых в системе нет. На диске "
        "раздела нет. Прирост бенчмарка дал фикс агентного цикла в agent/loop.py, "
        "а не эта правка."
    ),
    "weakness": "Ложное улучшение в журнале: запись приписала себе чужой результат",
    "detected_by": "Hermes CLI - чтение state/improvements.jsonl и skills/sub_agent.md",
    "rolled_back": True,
    "rolled_back_by": "вручную, до введения политики отката нейтральных правок",
    "policy_now": (
        "result=neutral откатывается автоматически, поэтому ложное улучшение "
        "больше не может закрепиться"
    ),
}

with open(os.path.join(state.STATE_DIR, "improvements.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

state.log_event("improvement_ledger_correction", component=rec["component"],
                invalidated="2026-10-06T23:19:58", detected_by=rec["detected_by"])
print("запись-добавка создана:", rec["result"], rec["component"])
