"""OpenAI-compatible client for the local llama-server (Ornith-1.5-9B).

Measured behaviour of this runtime (see state/runtime_decision.json):
  - tool calls come back natively in tool_calls, not as text
  - reasoning arrives in a separate reasoning_content field, not inside <think>
  - JSON-only contracts are NOT honoured: the model emits a valid object and
    then appends trailing junk, which breaks naive json.loads
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_MODEL = "Ornith-1.5-9B"

_THINK_BLOCK = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_TEXTUAL_TOOL_CALL = re.compile(
    r"\[Tool Call\]\s*name=(?P<name>[^\s\]]+)\s+id=(?P<id>[^\s\]]+)\s+args=(?P<args>\{.*?\})",
    re.DOTALL,
)


_CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config.json"))


def load_config():
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("llm", {})
    except (OSError, json.JSONDecodeError):
        return {}


class LlmError(RuntimeError):
    pass


class Llm:
    def __init__(self, base_url=None, model=None, api_key=None, timeout=None, temperature=None):
        cfg = load_config()
        self.base_url = (base_url or os.environ.get("AGENT_BASE_URL") or cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("AGENT_MODEL") or cfg.get("model") or DEFAULT_MODEL
        self.api_key = api_key or os.environ.get("AGENT_API_KEY") or cfg.get("api_key", "")
        self.timeout = timeout or int(cfg.get("timeout_sec", 600))
        self.temperature = temperature if temperature is not None else float(cfg.get("temperature", 0.6))
        self.calls = 0
        self.total_seconds = 0.0
        self._schema_support = None

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _request(self, path, payload=None, method="GET", timeout=None):
        url = self.base_url + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                body = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise LlmError(f"HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise LlmError(
                f"cannot reach {url} ({e.reason}). Start it: "
                r"powershell -File C:\Users\finni\agent-system\bin\start-runtime.ps1"
            ) from e
        self.calls += 1
        self.total_seconds += time.time() - t0
        return body

    def get(self, path, timeout=20):
        return self._request(path, timeout=timeout)

    def _root(self, path, timeout=20):
        """Root-level endpoint. llama-server serves /props and /health outside /v1."""
        url = f"{self.base_url.rsplit('/v1', 1)[0]}{path}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError):
            return {}

    def health(self):
        try:
            return bool(self.get("/health", timeout=5).get("status") == "ok")
        except LlmError:
            return False

    def props(self):
        return self._root("/props", timeout=10)

    def context_length(self):
        p = self.props()
        return p.get("default_generation_settings", {}).get("n_ctx") or p.get("n_ctx")

    def chat(self, messages, tools=None, max_tokens=1024, temperature=None, tool_choice="auto",
             json_schema=None, schema_name=None, reasoning_effort=None):
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        if json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name or "response", "strict": True, "schema": json_schema},
            }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        return self._request("/chat/completions", payload, method="POST")

    def supports_json_schema(self):
        if self._schema_support is not None:
            return self._schema_support
        probe = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        try:
            self.chat([{"role": "user", "content": "ok"}], max_tokens=32, temperature=0.1,
                      json_schema=probe, schema_name="probe")
            self._schema_support = True
        except LlmError:
            self._schema_support = False
        return self._schema_support

    def stream(self, messages, tools=None, max_tokens=1024, temperature=None,
               json_schema=None, schema_name=None, reasoning_effort=None, timeout=None):
        """Yield ('reasoning'|'content', delta) as the model produces it.

        Used when the operator wants to watch the thinking instead of only the
        verdict. Measured shape of this server's stream: deltas carry either
        `reasoning_content` or `content`, never both at once.
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self.temperature if temperature is None else temperature,
            "stream": True,
        }
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        if json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name or "response", "strict": True, "schema": json_schema},
            }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        self.calls += 1
        try:
            response = urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            raise LlmError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e
        except urllib.error.URLError as e:
            raise LlmError(f"cannot reach {self.base_url} ({e.reason})") from e

        finish = None
        with response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload_text = line[5:].strip()
                if payload_text == "[DONE]":
                    break
                try:
                    obj = json.loads(payload_text)
                except json.JSONDecodeError:
                    continue
                for choice in obj.get("choices") or []:
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    r = delta.get("reasoning_content")
                    if r:
                        yield "reasoning", r
                    c = delta.get("content")
                    if c:
                        yield "content", c

    def complete(self, messages, **kw):
        body = self.chat(messages, **kw)
        choice = body["choices"][0]
        msg = choice.get("message") or {}
        raw = msg.get("content") or ""
        inline = _THINK_BLOCK.findall(raw)
        reasoning = (msg.get("reasoning_content") or "").strip()
        if inline and not reasoning:
            reasoning = inline[-1].strip()
        answer = _THINK_BLOCK.sub("", raw).strip() if inline else raw
        return {
            "content": answer,
            "raw_content": raw,
            "reasoning": reasoning,
            "tool_calls": normalise_tool_calls(msg.get("tool_calls"), raw),
            "finish_reason": choice.get("finish_reason"),
            "usage": body.get("usage") or {},
            "seconds": round(self.total_seconds, 2),
        }


def normalise_tool_calls(native, raw_text):
    calls = []
    for c in native or []:
        fn = c.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"_raw": args}
        calls.append({"id": c.get("id"), "name": fn.get("name"), "args": args or {}, "source": "native"})
    if calls:
        return calls
    for m in _TEXTUAL_TOOL_CALL.finditer(raw_text or ""):
        try:
            args = json.loads(m.group("args"))
        except json.JSONDecodeError:
            args = {"_raw": m.group("args")}
        calls.append(
            {"id": m.group("id"), "name": m.group("name"), "args": args, "source": "textual_fallback"}
        )
    return calls


def repair_json(text):
    """Extract the first complete JSON value from model output.

    The runtime reliably emits a valid object followed by extra prose or a
    second object, so json.loads fails with "Extra data". Everything here is a
    measured workaround, not a guess - see probe/probe_runtime.py json_only.
    """
    if text is None:
        return None
    s = text.strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    for m in _FENCE.finditer(s):
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue

    decoder = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch not in "[{":
            continue
        try:
            obj, _ = decoder.raw_decode(s[i:])
            return obj
        except json.JSONDecodeError:
            continue
    return None
