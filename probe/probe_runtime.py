"""Probe the local runtime: context, reasoning, tool calling, JSON discipline."""
import json
import os
import time
import urllib.request

BASE = os.environ.get("AGENT_BASE_URL", "http://127.0.0.1:8080/v1")
MODEL = os.environ.get("AGENT_MODEL", "Ornith-1.5-9B")

_CONFIG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config.json"))


def _api_key():
    """Key lives in config.json, which is git-ignored. Never inline it here -
    this file is tracked and the repository is public."""
    if os.environ.get("AGENT_API_KEY"):
        return os.environ["AGENT_API_KEY"]
    try:
        with open(_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f).get("llm", {}).get("api_key", "")
    except (OSError, json.JSONDecodeError):
        return ""


KEY = _api_key()


def post(path, payload, timeout=180):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"},
        method="POST",
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8")), time.time() - t0


def get(path, timeout=20):
    req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def split_think(text):
    """Return (reasoning, answer). Reasoning is content INSIDE <think>, not the tag."""
    if not text:
        return "", ""
    i, j = text.find("<think>"), text.find("</think>")
    if i == -1 or j == -1 or j < i:
        return "", text
    return text[i + len("<think>") : j].strip(), text[j + len("</think>") :].strip()


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": "Run a shell command on Windows and return its output.",
            "parameters": {
                "type": "object",
                "properties": {"cmd": {"type": "string", "description": "command to run"}},
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "Read a file from disk.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
]


def probe_models():
    d = get("/models")
    meta = d["data"][0].get("meta", {})
    return {
        "id": d["data"][0]["id"],
        "n_ctx": meta.get("n_ctx"),
        "n_ctx_train": meta.get("n_ctx_train"),
        "n_params": meta.get("n_params"),
        "ftype": meta.get("ftype"),
        "size_gb": round((meta.get("size") or 0) / 1e9, 2),
    }


def probe_reasoning():
    body, dt = post(
        "/chat/completions",
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": "What is 17*23? Answer with the number."}],
            "max_tokens": 400,
            "temperature": 0.6,
        },
    )
    msg = body["choices"][0]["message"]
    raw = msg.get("content") or ""
    reasoning, answer = split_think(raw)
    # llama.cpp may return the trace in a dedicated field
    field_reason = (msg.get("reasoning_content") or "").strip()
    return {
        "seconds": round(dt, 1),
        "has_think_block": "<think>" in raw,
        "inline_reasoning_chars": len(reasoning),
        "field_reasoning_chars": len(field_reason),
        "reasoning_active": bool(reasoning or field_reason),
        "answer": answer[:120],
    }


def probe_tool_calling():
    body, dt = post(
        "/chat/completions",
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": "List the files in D:\\llm\\models. Use the tools."}],
            "tools": TOOLS,
            "tool_choice": "auto",
            "max_tokens": 512,
            "temperature": 0.6,
        },
    )
    msg = body["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    raw = msg.get("content") or ""
    out = {
        "seconds": round(dt, 1),
        "native_tool_calls": len(calls),
        "content_has_textual_marker": "[Tool Call]" in raw,
        "content_preview": raw[:300],
    }
    if calls:
        out["first_call"] = {
            "type": calls[0].get("type"),
            "function_name": (calls[0].get("function") or {}).get("name"),
            "arguments": (calls[0].get("function") or {}).get("arguments"),
        }
    return out


def probe_json_only():
    """Does the model honour a strict JSON-only contract? This is the single
    biggest risk for a self-improving loop, so measure it, do not assume it."""
    body, dt = post(
        "/chat/completions",
        {
            "model": MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "You output raw JSON only. No prose, no markdown fences.",
                },
                {
                    "role": "user",
                    "content": (
                        'Goal: "Read state/inventory.json and report the GPU name." '
                        'Return {"goal":string,"stages":[{"id":string,"goal":string,'
                        '"acceptance_criteria":string,"budget_steps":int}]} '
                        "with 1-2 stages."
                    ),
                },
            ],
            "max_tokens": 600,
            "temperature": 0.3,
        },
    )
    raw = body["choices"][0]["message"].get("content") or ""
    reasoning, answer = split_think(raw)
    verdict = {"seconds": round(dt, 1), "raw_preview": answer[:200]}
    try:
        obj = json.loads(answer)
        verdict["valid_json"] = True
        verdict["top_keys"] = sorted(obj.keys()) if isinstance(obj, dict) else "not a dict"
    except Exception as e:
        verdict["valid_json"] = False
        verdict["parse_error"] = str(e)[:160]
    return verdict


def main():
    out = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "base_url": BASE, "model": MODEL}
    for name, fn in (
        ("models", probe_models),
        ("reasoning", probe_reasoning),
        ("tool_calling", probe_tool_calling),
        ("json_only", probe_json_only),
    ):
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = {"error": f"{type(e).__name__}: {e}"}
        print(name, "->", json.dumps(out[name], ensure_ascii=False)[:400], flush=True)

    tc = out.get("tool_calling", {})
    rs = out.get("reasoning", {})
    out["runtime_decision"] = {
        "tool_call_mode": (
            "native" if tc.get("native_tool_calls", 0) > 0
            else "textual" if tc.get("content_has_textual_marker")
            else "none"
        ),
        "reasoning_mode": "active" if rs.get("reasoning_active") else "inactive",
        "json_only_reliable": bool(out.get("json_only", {}).get("valid_json")),
    }
    print("\nDECISION:", json.dumps(out["runtime_decision"], ensure_ascii=False))
    return out


if __name__ == "__main__":
    result = main()
    state_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "state", "runtime_decision.json"))
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("saved state/runtime_decision.json")
