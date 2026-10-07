"""Thermal and VRAM guard using nvidia-smi.

WinTmp was rejected: on Windows it only exposes the CPU package sensor, which
needs a driver that is not present here, and nvidia-smi is always available and
covers the part that actually throttles a llama.cpp generation loop.
"""

import json
import re
import subprocess
import time

OK, WARM, HOT, CRITICAL = "ok", "warm", "hot", "critical"
THRESHOLDS = {"warm": 70, "hot": 80, "critical": 85}
REST_SECONDS = {"warm": 0, "hot": 30, "critical": 120}


def _smi(query):
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def gpu_state():
    raw = _smi("temperature.gpu,memory.used,memory.total,utilization.gpu,power.draw")
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) < 5:
        return {"available": False, "temp_c": None, "used_mb": None, "total_mb": None}

    def num(s):
        m = re.search(r"-?\d+(?:\.\d+)?", s or "")
        return float(m.group()) if m else None

    temp, used, total, util, power = (num(p) for p in parts[:5])
    status = "unknown"
    if temp is not None:
        if temp >= THRESHOLDS["critical"]:
            status = CRITICAL
        elif temp >= THRESHOLDS["hot"]:
            status = HOT
        elif temp >= THRESHOLDS["warm"]:
            status = WARM
        else:
            status = OK
    return {
        "available": True,
        "temp_c": temp,
        "used_mb": int(used) if used is not None else None,
        "total_mb": int(total) if total is not None else None,
        "free_mb": int(total - used) if None not in (used, total) else None,
        "util_pct": util,
        "power_w": power,
        "status": status,
    }


def check():
    return gpu_state()


def rest(reason="", max_rounds=4):
    """Sleep until the GPU is back under the hot threshold. Returns waits taken."""
    waits = []
    for _ in range(max_rounds):
        s = gpu_state()
        if not s.get("available") or s["temp_c"] is None:
            return waits
        if s["temp_c"] < THRESHOLDS["hot"]:
            return waits
        seconds = 120 if s["temp_c"] >= THRESHOLDS["critical"] else 30
        waits.append(seconds)
        time.sleep(seconds)
    return waits


def gate(reason=""):
    """Call before every LLM turn. Returns (proceed, snapshot, waited_seconds)."""
    before = gpu_state()
    if before.get("available") and before.get("temp_c") is not None:
        if before["temp_c"] >= THRESHOLDS["hot"]:
            waited = sum(rest(reason))
            after = gpu_state()
            return after["temp_c"] < THRESHOLDS["critical"], after, waited
    return True, before, 0


def snapshot_series(interval=60, count=4):
    return [gpu_state() for _ in range(count) if not time.sleep(interval)]


def to_json(s):
    return json.dumps(s, ensure_ascii=False)
