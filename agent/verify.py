"""Verification with a deterministic first pass.

Measured failure this fixes: the Verify agent has no filesystem access, so the
"check deterministically first" instruction in its skill was unimplementable and
it answered `passed` on a stage that had changed nothing at all. An LLM judge
that cannot look cannot be trusted to gate a self-improvement loop, because it
would mark broken changes as improved.

So the loop checks what can be checked in code, and only falls back to the model
for criteria that leave nothing machine-checkable behind.
"""

import json
import os
import re

from runtime import state

ROOT = state.ROOT

FIELD_PATTERNS = [
    re.compile(r"\bполе\s+[\"'`‘]?([A-Za-z_][A-Za-z0-9_]*)[\"'`’]?", re.IGNORECASE),
    re.compile(r"\bfield\s+[\"'`‘]?([A-Za-z_][A-Za-z0-9_]*)[\"'`’]?", re.IGNORECASE),
    re.compile(r"\bключ\s+[\"'`‘]?([A-Za-z_][A-Za-z0-9_]*)[\"'`’]?", re.IGNORECASE),
    re.compile(r"\bkey\s+[\"'`‘]?([A-Za-z_][A-Za-z0-9_]*)[\"'`’]?", re.IGNORECASE),
]
PATH_PATTERN = re.compile(
    r"[\w.\\/:-]*[\w-]+\.(?:json|md|py|ps1|cmd|yaml|yml|txt|log)", re.IGNORECASE
)


def _abs(rel):
    return rel if os.path.isabs(rel) else os.path.join(ROOT, rel)


def _wanted_fields(criteria):
    fields = []
    for rx in FIELD_PATTERNS:
        fields.extend(rx.findall(criteria or ""))
    seen, out = set(), []
    for f in fields:
        if f.lower() not in seen:
            seen.add(f.lower())
            out.append(f)
    return out


def _json_targets(candidates, criteria):
    """Resolve JSON files a criterion talks about. Returns (targets, searched)."""
    targets, searched = [], []
    for rel in candidates:
        searched.append(str(rel))
        p = _abs(str(rel))
        if p.endswith(".json") and os.path.exists(p):
            targets.append((str(rel), p))
    if targets:
        return targets, searched
    for n in PATH_PATTERN.findall(criteria or ""):
        for cand in (n, os.path.join("state", os.path.basename(n)), n.replace("\\", "/")):
            p = _abs(cand)
            if p.endswith(".json"):
                searched.append(cand)
                if os.path.exists(p):
                    targets.append((cand, p))
                    return targets, searched
                break
    return targets, searched


def _field_report(criteria, entry):
    """Never pass silently. A field that cannot be found because the file is
    absent is a failure, not an absence of evidence."""
    fields = _wanted_fields(criteria)
    if not fields:
        return None
    candidates = [str(x) for x in (entry.get("files_changed") or []) + (entry.get("artifacts") or [])]
    targets, searched = _json_targets(candidates, criteria)

    if not targets:
        return {
            "check": "json_fields_present",
            "passed": False,
            "fields": fields,
            "file": None,
            "searched": searched,
            "missing": [{"error": "json-файл не найден ни среди файлов, объявленных субагентом, ни в критерии"}],
        }

    missing = []
    for rel, path in targets:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            missing.append({"file": rel, "error": f"не читается как JSON: {str(e)[:120]}"})
            continue
        for name in fields:
            found = name in data if isinstance(data, dict) else False
            if not found and isinstance(data, dict):
                for value in data.values():
                    if isinstance(value, dict) and name in value:
                        found = True
                        break
                    if isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict) and name in item:
                                found = True
                                break
                    if found:
                        break
            if not found:
                missing.append({"file": rel, "field": name})

    return {
        "check": "json_fields_present",
        "passed": not missing,
        "fields": fields,
        "file": targets[0][0],
        "missing": missing,
    }


def _declared_but_absent(entry):
    declared = [str(x) for x in (entry.get("files_changed") or []) + (entry.get("artifacts") or [])]
    return [d for d in declared if not os.path.exists(_abs(d))]


def deterministic(criteria, entry):
    """Return (verdict, checks, applicable).

    verdict is None when nothing machine-checkable was found, which means the
    model has to decide.
    """
    checks = []
    candidates = [str(x) for x in (entry.get("files_changed") or []) + (entry.get("artifacts") or [])]

    absent = _declared_but_absent(entry)
    if absent:
        checks.append({"check": "declared_files_exist", "passed": False, "missing": absent})
    elif candidates:
        checks.append({"check": "declared_files_exist", "passed": True, "count": len(candidates)})
    else:
        checks.append({"check": "work_declared", "passed": False,
                       "error": "субагент не объявил ни одного изменённого файла или артефакта"})

    field_check = _field_report(criteria, entry)
    if field_check:
        checks.append(field_check)

    if not checks:
        return None, [], False
    return all(c["passed"] for c in checks), checks, True


def evidence_bundle(entry, limit=1200):
    lines = []
    for rel in (entry.get("files_changed") or []) + (entry.get("artifacts") or []):
        p = _abs(str(rel))
        if os.path.exists(p):
            size = os.path.getsize(p)
            head = ""
            if p.endswith((".json", ".md", ".txt")):
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        head = f.read(400).replace("\n", " ")[:400]
                except OSError:
                    head = ""
            lines.append(f"- {rel}: {size} Б" + (f" | начало: {head}" if head else ""))
        else:
            lines.append(f"- {rel}: ОТСУТСТВУЕТ")
    return "\n".join(lines)[:limit] or "(нет файлов)"


def judge_with_model(client, loop_module, criteria, entry, checks):
    """Model decides only the part that code cannot. Evidence is attached so the
    judge is reasoning over facts instead of over the sub-agent's self-report."""
    facts = {
        "criteria": criteria,
        "files_declared": entry.get("files_changed") or [],
        "artifacts": entry.get("artifacts") or [],
        "sub_agent_summary": (entry.get("summary") or "")[:400],
        "deterministic_checks": checks,
        "files_on_disk": evidence_bundle(entry),
    }
    prompt = (
        "Детерминированные проверки уже выполнены, их результат ниже.\n"
        "Оцени только то, что проверить кодом было нельзя.\n\n"
        f"{json.dumps(facts, ensure_ascii=False, indent=2)}"
    )
    verdict, meta = loop_module.ask_json(client, "verify", prompt, max_tokens=500)
    return verdict, meta
