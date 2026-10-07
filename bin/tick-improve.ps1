param([int]$Changes = 1)
$ErrorActionPreference = 'Continue'

# Обёртка для планировщика. Задача вызывает только этот файл одной строкой
# без кавычек и перенаправлений - inline-команда с >> ломалась при регистрации
# (задача падала с 0x1, лог не создавался).
$root = 'C:\Users\finni\agent-system'
$py = 'C:\Users\finni\AppData\Local\Programs\Python\Python312\python.exe'
$log = Join-Path $root 'logs\improve-hourly.log'

New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function Say($m) { Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding UTF8 }

Say '=== старт цикла самоулучшения ==='

if (-not (Test-Path $py)) { Say "нет интерпретатора: $py"; exit 1 }

$before = (Get-Item $log).Length
& $py (Join-Path $root 'agent\self_improve.py') $Changes 2>&1 |
  ForEach-Object { Add-Content -LiteralPath $log -Value $_ -Encoding UTF8 }

$code = $LASTEXITCODE
$after = (Get-Item $log).Length
Say ("=== конец, код {0}, записано {1} байт ===" -f $code, ($after - $before))
exit $code
