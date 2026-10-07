"""Persistent state: event log, metrics, improvement ledger, backups.

Everything the self-improvement loop needs to notice its own regressions lives
here. Append-only where it matters, so a crashed cycle never destroys history.
"""

import json
import os
import shutil
import time
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATE_DIR = os.path.join(ROOT, "state")
BACKUP_DIR = os.path.join(STATE_DIR, "backups")
EVENT_LOG = os.path.join(STATE_DIR, "events.jsonl")
METRICS = os.path.join(STATE_DIR, "metrics.json")
IMPROVEMENTS = os.path.join(STATE_DIR, "improvements.jsonl")


def ensure_dirs():
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_event(kind, **fields):
    ensure_dirs()
    rec = {"ts": now(), "kind": kind, **fields}
    with open(EVENT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read_events(since_minutes=None, limit=500, kinds=None):
    if not os.path.exists(EVENT_LOG):
        return []
    cutoff = None
    if since_minutes:
        cutoff = time.time() - since_minutes * 60
    out = []
    with open(EVENT_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if cutoff is not None:
                try:
                    if datetime.fromisoformat(rec["ts"]).timestamp() < cutoff:
                        continue
                except (KeyError, ValueError):
                    pass
            if kinds and rec.get("kind") not in kinds:
                continue
            out.append(rec)
    return out[-limit:]


def log_improvement(component, change, reason, result, **extra):
    ensure_dirs()
    rec = {
        "timestamp": now(),
        "component": component,
        "change": change,
        "reason": reason,
        "result": result,
        **extra,
    }
    with open(IMPROVEMENTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read_improvements(limit=50):
    if not os.path.exists(IMPROVEMENTS):
        return []
    out = []
    with open(IMPROVEMENTS, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out[-limit:]


def load_metrics():
    if not os.path.exists(METRICS):
        return {}
    try:
        with open(METRICS, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_metrics(m):
    ensure_dirs()
    tmp = METRICS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    os.replace(tmp, METRICS)
    return m


def merge_metrics(patch):
    m = load_metrics()
    m.update(patch)
    m["updated_at"] = now()
    return save_metrics(m)


def backup(path):
    ensure_dirs()
    if not os.path.exists(path):
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}__{os.path.basename(path)}"
    dest = os.path.join(BACKUP_DIR, name)
    shutil.copy2(path, dest)
    return dest


def restore(backup_path):
    original = os.path.join(ROOT, backup_path.split("__", 1)[1])
    if not os.path.exists(original):
        return False
    shutil.copy2(backup_path, original)
    return original
