$ErrorActionPreference = 'Continue'

$root = 'C:\Users\finni\agent-system'
$py = 'C:\Users\finni\AppData\Local\Programs\Python\Python312\python.exe'
$log = Join-Path $root 'logs\version-daily.log'

New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function Say($m) { Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding UTF8 }

Say '=== снимок версии и отправка в GitHub ==='
if (-not (Test-Path $py)) { Say "нет интерпретатора: $py"; exit 1 }

$env:PYTHONIOENCODING = 'utf-8'
$env:GIT_TERMINAL_PROMPT = '0'

& $py (Join-Path $root 'agent\version_agent.py') snapshot --bump patch --push 2>&1 |
  ForEach-Object { Add-Content -LiteralPath $log -Value $_ -Encoding UTF8 }
$code = $LASTEXITCODE

# Без интернета или без прав на push это не повод считать задачу упавшей:
# снимок и тег уже созданы локально, уйдёт при следующем запуске.
if ($code -ne 0) {
  Say "push не удался (код $code) - снимок сохранён локально, повторится завтра"
  & $py (Join-Path $root 'agent\version_agent.py') status 2>&1 |
    ForEach-Object { Add-Content -LiteralPath $log -Value $_ -Encoding UTF8 }
}
Say "=== конец, код $code ==="
exit 0
