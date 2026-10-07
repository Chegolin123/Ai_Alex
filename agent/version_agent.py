"""Version agent: snapshots the system, writes a report, tags, pushes to GitHub.

The self-improvement loop already keeps per-file backups in state/backups. This
is the repository-level counterpart: a commit per snapshot with a report that
says what changed, what the metrics did, and which improvement attempts were kept
or rolled back. A tag is the rollback target for the whole system.

Git is not installed system-wide on this machine. The portable Git that ships
inside the Hermes toolchain is used instead, resolved by full path, so the agent
does not depend on PATH.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent import metrics as metrics_mod
from rag import index as rag
from runtime import llm, state, thermal

ROOT = state.ROOT
REPORTS_DIR = os.path.join(ROOT, "reports")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
VERSION_FILE = os.path.join(ROOT, "state", "version.json")
REMOTE_URL = "https://github.com/Chegolin123/Ai_Alex.git"

PORTABLE_GIT_DIRS = [
    r"C:\Users\finni\AppData\Local\hermes\tools\git-2.53.0+3-win32-x64\cmd\git.exe",
    r"C:\Program Files\Git\cmd\git.exe",
]

SECRET_FILES = ["config.json", "config.local.json", ".env"]

# Only a literal long enough to actually be a credential counts. Reading a key
# out of config (`cfg.get("api_key", "")`) is not a leak and must not be flagged.
SECRET_PATTERNS = [
    re.compile(r"(api[_-]?key|token|secret|password|bearer)\s*[:=]\s*[\"']?([A-Za-z0-9_\-]{24,})", re.I),
]


def git_bin():
    for p in PORTABLE_GIT_DIRS:
        if os.path.exists(p):
            return p
    from shutil import which
    found = which("git")
    if found:
        return found
    raise RuntimeError("git не найден ни в тулчейне Hermes, ни в PATH")


class Git:
    def __init__(self, root=ROOT):
        self.root = root
        self.bin = git_bin()
        self.env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")

    def run(self, *args, check=True, timeout=180, capture=True):
        p = subprocess.run(
            [self.bin, "-C", self.root, *args],
            capture_output=capture, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=self.env,
        )
        if check and p.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} -> {p.returncode}: {(p.stderr or '').strip()[:300]}")
        return p

    def out(self, *args, **kw):
        return (self.run(*args, **kw).stdout or "").strip()

    def available(self):
        return (self.run("rev-parse", "--git-dir", check=False).returncode == 0)

    def init(self):
        self.run("init", "-b", "main")
        return self

    def set_remote(self, url=REMOTE_URL):
        existing = self.run("remote", check=False)
        if "origin" in (existing.stdout or ""):
            self.run("remote", "set-url", "origin", url)
        else:
            self.run("remote", "add", "origin", url)
        return self.out("remote", "-v")

    def status(self):
        branch = self.out("rev-parse", "--abbrev-ref", "HEAD", check=False) or "-"
        dirty = self.out("status", "--porcelain")
        head = self.out("rev-parse", "--short", "HEAD", check=False)
        tags = self.out("tag", "--list").splitlines()
        unpushed = self.run("log", "--oneline", "@{u}..HEAD", check=False)
        return {
            "branch": branch,
            "head": head or "(нет коммитов)",
            "dirty_files": [l[3:] for l in dirty.splitlines() if l.strip()],
            "tags": tags,
            "latest_tag": tags[-1] if tags else None,
            "unpushed": (unpushed.stdout or "").strip().splitlines() if unpushed.returncode == 0 else None,
            "remote": self.out("remote", "get-url", "origin", check=False) or None,
        }

    def commit(self, message, add_all=True):
        if add_all:
            self.run("add", "-A")
        staged = self.out("diff", "--cached", "--name-only")
        if not staged:
            return None
        self.run("-c", "core.autocrlf=false", "commit", "-m", message)
        return self.out("rev-parse", "--short", "HEAD")

    def tag(self, name, message=None):
        self.run("tag", "-a", name, "-m", message or name)
        return name

    def push(self, tags=True, timeout=300):
        p = subprocess.run(
            [self.bin, "-C", ROOT, "push", "-u", "origin", "main"] + (["--follow-tags"] if tags else []),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            env=dict(os.environ, GIT_TERMINAL_PROMPT="1", LC_ALL="C"),
        )
        return {"ok": p.returncode == 0, "returncode": p.returncode,
                "stdout": (p.stdout or "").strip()[-2000:],
                "stderr": (p.stderr or "").strip()[-2000:]}


def version():
    if os.path.exists(VERSION_FILE):
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {"major": 0, "minor": 1, "patch": 0, "bump": "minor"}


def save_version(v):
    state.ensure_dirs()
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump(v, f, ensure_ascii=False, indent=2)
    return v


def bump(v, kind="minor"):
    v = dict(v)
    v["bump"] = kind
    if kind == "major":
        v["major"] += 1; v["minor"] = 0; v["patch"] = 0
    elif kind == "patch":
        v["patch"] += 1
    else:
        v["minor"] += 1; v["patch"] = 0
    return v


def build_report(g, st, m, improvements, v, tag):
    client = llm.Llm()
    up = client.health()
    gpu = thermal.check()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    c, t, th = m["counters"], m["task_metrics"], m["thermal_metrics"]
    lines = [
        f"# Отчёт системы ALEX — {now}",
        "",
        f"Тег: `{tag}`  ·  ветка: `{st['branch']}`  ·  коммит: `{st['head']}`",
        "",
        "## Состояние",
        "",
        "| Что | Значение |",
        "|---|---|",
        f"| LLM-сервер | {'работает' if up else 'НЕ РАБОТАЕТ'} |",
        f"| Модель | {client.model} |",
        f"| Контекст | {client.context_length() or 'н/д'} |",
        f"| GPU | {gpu.get('temp_c')} °C, {gpu.get('status')} |",
        f"| VRAM | {gpu.get('used_mb')} / {gpu.get('total_mb')} МБ |",
        f"| RAG | {m['rag']['files']} файлов, {m['rag']['chunks']} чанков |",
        "",
        "## Метрики",
        "",
        "| Метрика | Значение |",
        "|---|---|",
        f"| Циклов | {c['cycles']} |",
        f"| Успешных циклов | {t['success_rate']} |",
        f"| Надёжность JSON | {t['json_reliability']} |",
        f"| Попыток на этап | {t['avg_attempts_per_stage']} |",
        f"| Время цикла | {t['avg_time_per_cycle_sec']} с |",
        f"| Неизвестных tool-call | {c['tool_call_unknown']} |",
        f"| Заблокировано опасных команд | {c['shell_blocked_by_safety']} |",
        f"| Троттлинг по температуре | {th['throttle_events']} |",
        "",
        "## Самоулучшение",
        "",
    ]
    if improvements:
        lines += ["| Время | Компонент | Результат | Дефект |", "|---|---|---|---|"]
        for h in improvements[-8:]:
            mark = {"improved": "улучшено", "regressed": "регресс",
                    "neutral": "нейтрально", "rejected_invalid": "отклонено"}.get(h.get("result"), h.get("result"))
            weak = (h.get("weakness") or "").replace("|", "/")[:80]
            lines.append(f"| {h.get('timestamp', '')[:16]} | `{h.get('component')}` | {mark} | {weak} |")
    else:
        lines.append("Записей пока нет.")

    if st["dirty_files"]:
        lines += ["", "## Незакоммиченные файлы", ""]
        lines += [f"- `{f}`" for f in st["dirty_files"][:30]]

    lines += ["", f"_Снимок создан агентом версий {state.now()}_", ""]
    return "\n".join(lines)


def update_changelog(tag, message, st, v):
    entry = [f"## {tag} — {datetime.now().strftime('%Y-%m-%d %H:%M')}", "", f"- {message}",
             f"- коммит `{st['head']}`", ""]
    old = ""
    if os.path.exists(CHANGELOG):
        with open(CHANGELOG, "r", encoding="utf-8") as f:
            old = f.read()
    header = "# История версий\n\n"
    if old.startswith(header):
        old = old[len(header):]
    with open(CHANGELOG, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(entry) + "\n" + old)
    return True


def doctor(g):
    """Fail loudly if a secret could ever reach the repository."""
    problems = []
    tracked = g.out("ls-files").splitlines() if g.available() else []
    for name in SECRET_FILES:
        if name in tracked:
            problems.append(f"{name} в индексе git - секрет утечёт в репозиторий")
    for path in tracked:
        if os.path.exists(os.path.join(ROOT, path)) and path.endswith((".py", ".json", ".yaml", ".md")):
            try:
                text = open(os.path.join(ROOT, path), "r", encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if path == "config.json":
                continue
            for rx in SECRET_PATTERNS:
                for m in rx.finditer(text):
                    problems.append(f"похоже на секрет в {path}: {m.group(2)[:12]}... ({len(m.group(2))} симв.)")
                    break
    return problems


def cmd_status(g):
    st = g.status()
    print(json.dumps(st, ensure_ascii=False, indent=2))
    v = version()
    print(f"версия: {v['major']}.{v['minor']}.{v['patch']}")
    problems = doctor(g)
    print("секреты: " + ("ОК" if not problems else "ПРОБЛЕМЫ"))
    for p in problems:
        print("  " + p)
    return st


def cmd_snapshot(g, bump_kind="minor", message=None, push=False):
    g.init() if not g.available() else None
    if not g.out("remote", "get-url", "origin", check=False):
        g.set_remote()

    try:
        rag.index_paths()
    except Exception:
        pass
    m = metrics_mod.collect()
    improvements = state.read_improvements(limit=50)

    v = bump(version(), bump_kind)
    tag = f"v{v['major']}.{v['minor']}.{v['patch']}"
    st_before = g.status()
    msg = message or f"snapshot {tag}: циклов {m['counters']['cycles']}, успешных {m['task_metrics']['success_rate']}, JSON {m['task_metrics']['json_reliability']}"

    head = g.commit(msg)
    st = g.status()
    st["dirty_files"] = st_before["dirty_files"]

    os.makedirs(REPORTS_DIR, exist_ok=True)
    report_path = os.path.join(REPORTS_DIR, f"{datetime.now().strftime('%Y-%m-%d_%H%M')}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(build_report(g, st, m, improvements, v, tag))

    update_changelog(tag, msg, st, v)
    save_version(v)
    g.commit(f"отчёт и changelog: {tag}")
    g.tag(tag, msg)

    result = {"tag": tag, "report": os.path.relpath(report_path, ROOT),
              "head": head or g.out("rev-parse", "--short", "HEAD"),
              "files_committed": len(st_before["dirty_files"]),
              "pushed": False}
    if push:
        r = g.push()
        result.update({"pushed": r["ok"], "push_output": r["stdout"] or r["stderr"]})
    return result


def cmd_push(g):
    r = g.push()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 1


def cmd_history(g, limit=15):
    log = g.out("log", f"-{limit}", "--pretty=format:%h|%ad|%s", "--date=short").splitlines()
    tags = g.out("tag", "--list", "--sort=-creatordate").splitlines()
    print("теги:")
    for t in tags[:limit]:
        print(f"  {t}")
    print("\nкоммиты:")
    for line in log:
        parts = line.split("|", 2)
        print("  " + " | ".join(parts))


def cmd_rollback(g, tag):
    if not g.out("tag", "--list").splitlines():
        print("тегов нет - откатываться некуда")
        return 1
    st = g.status()
    if st["dirty_files"]:
        g.run("stash", "push", "-u", "-m", "перед откатом")
    g.run("reset", "--hard", tag)
    print(f"система возвращена на {tag}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Агент версий системы ALEX")
    ap.add_argument("cmd", choices=["status", "snapshot", "push", "history", "rollback", "doctor"])
    ap.add_argument("--bump", choices=["major", "minor", "patch"], default="minor")
    ap.add_argument("--message", default=None)
    ap.add_argument("--tag", default=None, help="для rollback: имя тега")
    ap.add_argument("--push", action="store_true", help="отправить в GitHub после снимка")
    a = ap.parse_args()

    g = Git()
    if a.cmd == "status":
        cmd_status(g)
    elif a.cmd == "snapshot":
        print(json.dumps(cmd_snapshot(g, a.bump, a.message, a.push), ensure_ascii=False, indent=2))
    elif a.cmd == "push":
        sys.exit(cmd_push(g))
    elif a.cmd == "history":
        cmd_history(g)
    elif a.cmd == "rollback":
        sys.exit(cmd_rollback(g, a.tag))
    elif a.cmd == "doctor":
        problems = doctor(g)
        print("секреты: " + ("ОК" if not problems else "ПРОБЛЕМЫ"))
        for p in problems:
            print("  " + p)
        sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
