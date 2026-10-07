"""Question backlog: what the autonomous agents could not decide on their own.

The self-improvement loop runs hourly without a human present. When it hits
something that needs a decision - a value it must not invent, a tradeoff between
two valid goals, a change that would be destructive - it parks the question here
instead of guessing. The mediator agent in Hermes is what reads this backlog out
to the human.

Append-only, same as the improvement ledger: history is the point.
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from runtime import state

QUESTIONS = os.path.join(state.STATE_DIR, "questions.jsonl")
OPEN = "open"
ANSWERED = "answered"
DISMISSED = "dismissed"


def _load():
    rows = []
    if not os.path.exists(QUESTIONS):
        return rows
    with open(QUESTIONS, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _append(rec):
    state.ensure_dirs()
    with open(QUESTIONS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _next_id(rows):
    used = 0
    for r in rows:
        raw = str(r.get("id", ""))
        if raw.startswith("q") and raw[1:].isdigit():
            used = max(used, int(raw[1:]))
    return f"q{used + 1:03d}"


def ask(question, source="self-improvement", context="", options=None, priority="normal"):
    rows = _load()
    open_same = [r for r in rows if r.get("status") == OPEN and r.get("question") == question]
    if open_same:
        return {"created": False, "reason": "такой вопрос уже открыт",
                "id": open_same[-1]["id"]}
    rec = {
        "id": _next_id(rows),
        "ts": state.now(),
        "status": OPEN,
        "source": source,
        "question": question,
        "context": context[:1200],
        "options": options or [],
        "priority": priority,
        "answer": None,
        "answered_at": None,
    }
    _append(rec)
    state.log_event("question_asked", qid=rec["id"], source=source, priority=priority)
    return {"created": True, **rec}


def listing(status=None, limit=50):
    rows = _load()
    if status:
        rows = [r for r in rows if r.get("status") == status]
    order = {"high": 0, "normal": 1, "low": 2}
    rows.sort(key=lambda r: (order.get(r.get("priority"), 1), r.get("ts") or ""), reverse=False)
    return rows[-limit:]


def answer(qid, text, by="human"):
    rows = _load()
    for r in rows:
        if r.get("id") == qid:
            r["status"] = ANSWERED
            r["answer"] = text[:2000]
            r["answered_at"] = state.now()
            r["answered_by"] = by
            _append(r)
            state.log_event("question_answered", qid=qid, by=by)
            return {"ok": True, "id": qid, "answer": text[:400]}
    return {"ok": False, "error": f"вопрос {qid} не найден"}


def dismiss(qid, why=""):
    rows = _load()
    for r in rows:
        if r.get("id") == qid:
            r["status"] = DISMISSED
            r["answer"] = why[:2000]
            r["answered_at"] = state.now()
            r["answered_by"] = "system"
            _append(r)
            return {"ok": True, "id": qid}
    return {"ok": False, "error": f"вопрос {qid} не найден"}


def counts():
    rows = _load()
    out = {OPEN: 0, ANSWERED: 0, DISMISSED: 0}
    for r in rows:
        out[r.get("status", OPEN)] = out.get(r.get("status", OPEN), 0) + 1
    out["total"] = len(rows)
    return out
