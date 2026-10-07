$ErrorActionPreference = 'Continue'

$root = 'C:\Users\finni\agent-system'
$py = 'C:\Users\finni\AppData\Local\Programs\Python\Python312\python.exe'
$log = Join-Path $root 'logs\metrics-daily.log'

New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function Say($m) { Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding UTF8 }

Say '=== метрики и индекс RAG ==='
if (-not (Test-Path $py)) { Say "нет интерпретатора: $py"; exit 1 }

& $py (Join-Path $root 'agent\metrics.py') 2>&1 |
  ForEach-Object { Add-Content -LiteralPath $log -Value $_ -Encoding UTF8 }
$code = $LASTEXITCODE
Say "=== конец, код $code ==="
exit $code
