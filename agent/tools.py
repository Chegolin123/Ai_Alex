"""Agent tools: shell, file IO, RAG. Every shell call passes the safety gate."""

import json
import os
import time

from rag import index as rag
from runtime import safety, state, thermal

ROOT = state.ROOT
ARTIFACTS = os.path.join(ROOT, "artifacts")
MAX_OUTPUT = 4000


def _clip(text, limit=MAX_OUTPUT):
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f"\n... [{len(text) - limit} симв. обрезано]"


def shell_exec(cmd, cwd=None, timeout=None):
    gate, snap, waited = thermal.gate("shell_exec")
    if not gate:
        return {"ok": False, "error": "thermal gate refused the call", "gpu": snap, "waited_sec": waited}
    result = safety.run(cmd, cwd=cwd or ROOT, timeout=timeout)
    result["stdout"] = _clip(result.get("stdout"))
    result["stderr"] = _clip(result.get("stderr"), 1500)
    result["gpu"] = {"temp_c": snap.get("temp_c"), "status": snap.get("status")}
    result["waited_sec"] = waited
    state.log_event(
        "shell_exec",
        cmd=cmd[:300],
        ok=result.get("ok"),
        blocked=result.get("blocked"),
        returncode=result.get("returncode"),
        reason=result.get("reason") if result.get("blocked") else None,
        temp_c=snap.get("temp_c"),
    )
    return result


def file_read(path, max_bytes=20000):
    full = path if os.path.isabs(path) else os.path.join(ROOT, path)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(max_bytes)
        state.log_event("file_read", path=path, ok=True)
        return {"ok": True, "path": full, "content": text}
    except OSError as e:
        state.log_event("file_read", path=path, ok=False, error=str(e))
        return {"ok": False, "path": full, "error": str(e)}


def file_write(path, content, backup=True):
    full = path if os.path.isabs(path) else os.path.join(ROOT, path)
    saved = state.backup(full) if backup else None
    try:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        state.log_event("file_write", path=path, ok=False, error=str(e))
        return {"ok": False, "error": str(e), "backup": saved}
    state.log_event("file_write", path=path, ok=True, bytes=len(content), backup=saved)
    return {"ok": True, "path": full, "bytes": len(content), "backup": saved}


def artifact_write(name, content):
    os.makedirs(ARTIFACTS, exist_ok=True)
    return file_write(os.path.join("artifacts", name), content, backup=False)


def rag_search(query, limit=4):
    hits = rag.search(query, limit=limit)
    state.log_event("rag_search", query=query[:120], hits=len(hits))
    return {"ok": True, "query": query, "hits": hits}


def gpu_status():
    s = thermal.check()
    state.log_event("gpu_status", temp_c=s.get("temp_c"), used_mb=s.get("used_mb"))
    return s


SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": "Run a shell command on this Windows machine. Returns exit code, stdout tail, stderr tail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "command line to run"},
                    "cwd": {"type": "string", "description": "optional working directory"},
                },
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "Read a text file from the agent system directory or an absolute path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "Write a text file. A timestamped backup of the previous version is kept.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_search",
            "description": "Full-text search across the agent system's own files: skills, runtime code, logs, state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gpu_status",
            "description": "Current GPU temperature, VRAM use and thermal status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_BY_NAME = {
    "shell_exec": lambda a: shell_exec(**a),
    "file_read": lambda a: file_read(**a),
    "file_write": lambda a: file_write(**a),
    "rag_search": lambda a: rag_search(**a),
    "gpu_status": lambda a: gpu_status(),
}


def dispatch(call):
    name, args = call.get("name"), call.get("args") or {}
    fn = _BY_NAME.get(name)
    if not fn:
        state.log_event("tool_call_unknown", name=name, args_keys=list(args))
        return {"ok": False, "error": f"unknown tool: {name}"}
    try:
        return fn(args)
    except TypeError as e:
        state.log_event("tool_call_bad_args", name=name, error=str(e))
        return {"ok": False, "error": f"bad arguments for {name}: {e}"}


def as_tools():
    return SCHEMA
