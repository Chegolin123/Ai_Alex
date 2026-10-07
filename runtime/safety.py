"""Destructive-command gate.

The shell tool is available to the agent, so the floor is: refuse the commands
that cannot be undone and cannot be confirmed afterwards. This runs before any
command reaches the shell and is not overridable by the model.
"""

import re
import shlex
import subprocess

BLOCKED_PATTERNS = [
    (r"\brm\s+(-[a-zA-Z]*\s+)*(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+.*[/\\]\s*$",
     "recursive delete of a filesystem root"),
    (r"\brm\s+-rf\s+[/\\]\s*$", "recursive delete of filesystem root"),
    (r"(?:^|[;&|]\s*)rm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+(?:/|/\*|~|~/\*|--no-preserve-root)(?=\s|$)",
     "recursive delete of filesystem root"),
    # Only a real disk format. Measured false positive: `--format=csv` and
    # `Format-Table` matched a bare "format" word and blocked healthy commands.
    (r"(?:^|[;&|]\s*)format\s+(?:[a-zA-Z]:|[/\\]|\d|/)", "disk format"),
    (r"(?:^|[;&|]\s*)mkfs(?:\.\w+)?\s", "filesystem format"),
    (r"\bdiskpart\b", "diskpart"),
    (r"\bdd\s+.*\bof=\s*[/\\](dev|mmcblkdisk|nvme)", "raw write to a block device"),
    (r":\(\)\s*\{.*\|.*&.*\}\s*;?\s*:", "fork bomb"),
    (r"\bdel\s+/[a-zA-Z]\s+.*\\\*\.(bat|cmd|exe|dll)", "mass delete of executables"),
    (r"\bRemove-Item\b.*-Recurse.*\bC:\\\s*$", "recursive delete of drive root"),
    (r"\bcipher\s+/w", "wipe free space"),
    (r"\b(vssadmin\s+delete\s+shadows|bcdedit.*recoveryenabled\s+no)", "destroy recovery points"),
    (r"(?:^|[;&|]\s*)(?:shutdown|halt|poweroff)\b", "power off"),
    (r"\bstop-computer\b|\brestart-computer\b", "power state change"),
]

PATTERNS = [(re.compile(p, re.IGNORECASE | re.DOTALL), why) for p, why in BLOCKED_PATTERNS]

DOWNLOAD_PIPE = re.compile(
    r"\b(curl|wget|iwr|invoke-webrequest|invoke-restmethod)\b[^|;]*\|\s*(iex|invoke-expression|powershell|cmd|bash|sh)\b",
    re.IGNORECASE,
)


def classify(cmd):
    """Return (allowed, reason)."""
    if not cmd or not cmd.strip():
        return False, "empty command"
    for rx, why in PATTERNS:
        if rx.search(cmd):
            return False, f"blocked: {why}"
    if DOWNLOAD_PIPE.search(cmd):
        return False, "blocked: piping downloaded content straight into a shell"
    if cmd.strip().lower().startswith("del /f /s /q c:\\"):
        return False, "blocked: mass delete on system drive"
    return True, "ok"


def _timeout_for(cmd):
    low = cmd.lower()
    return 600 if any(k in low for k in ("ollama", "pull", "install", "build", "pip ", "uv ")) else 120


def _wrap(cmd):
    """Run through PowerShell, not cmd.exe.

    Measured: the skills instruct the model in PowerShell (Test-Path,
    Get-ChildItem, Select-String), but shell=True on Windows resolves to cmd.exe,
    where every one of those is "not recognized" and the command fails. The
    agent was being told it had a shell it did not have.
    """
    return [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-Command",
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + cmd,
    ]


def run(cmd, cwd=None, timeout=None):
    allowed, reason = classify(cmd)
    if not allowed:
        return {"ok": False, "blocked": True, "reason": reason, "stdout": "", "stderr": "", "cmd": cmd}
    try:
        p = subprocess.run(
            _wrap(cmd),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or _timeout_for(cmd),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "blocked": False,
                "reason": f"timeout after {timeout or _timeout_for(cmd)}s",
                "stdout": "", "stderr": "", "cmd": cmd}
    return {
        "ok": p.returncode == 0,
        "blocked": False,
        "returncode": p.returncode,
        "stdout": (p.stdout or "")[-8000:],
        "stderr": (p.stderr or "")[-4000:],
        "cmd": cmd,
    }
