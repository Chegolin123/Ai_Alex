param([switch]$SkipHermes)
$ErrorActionPreference = 'Continue'

# Все административные задачи в одном запуске - одно окно UAC.
#   1. автозапуск агентной системы при старте Windows
#   2. ремонт тулчейна Hermes (пустые папки python/node/git/uv/...)
#   3. ярлыки рабочего/игрового режима на общем рабочем столе
$ErrorActionPreference = 'Continue'
$root = 'C:\Users\finni\agent-system'
$log  = Join-Path $root 'logs\setup-admin.log'

New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
Add-Content -Path $log -Value "=== запуск $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" -Encoding UTF8

function Test-IsAdmin {
  ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
  Add-Content -Path $log -Value 'нет прав администратора, запрашиваю UAC' -Encoding UTF8
  $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
  if ($SkipHermes) { $argList += '-SkipHermes' }
  Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argList
  exit 0
}

"" | Set-Content -Path $log -Encoding UTF8
function Say($m) { Write-Host $m; Add-Content -Path $log -Value $m -Encoding UTF8 }

Say '=== АДМИНИСТРАТИВНАЯ НАСТРОЙКА АГЕНТНОЙ СИСТЕМЫ ==='
Say "время: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Say ''

# 1. Автозапуск
Say '--- 1/3  автозапуск при старте Windows ---'
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'bin\register-autostart.ps1') -Mode enable 2>&1 |
  ForEach-Object { Add-Content -Path $log -Value $_ -Encoding UTF8 }

Say ''
if (-not $SkipHermes) {
  # 2. Ремонт тулчейна
  Say '--- 2/3  ремонт тулчейна Hermes (скачивание, займёт несколько минут) ---'
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'bin\repair-hermes.ps1') 2>&1 |
    ForEach-Object { Add-Content -Path $log -Value $_ -Encoding UTF8 }

  $env:Path = "C:\Users\finni\AppData\Local\hermes\bin;$env:Path"
  Say ''
  Say 'проверка hermes CLI:'
  try {
    $v = & hermes --version 2>&1 | Out-String
    Say "  $($v.Trim())"
  } catch {
    Say "  не отвечает: $($_.Exception.Message)"
  }
} else {
  Say '--- 2/3  ремонт Hermes пропущен (-SkipHermes) ---'
}

Say ''
Say '--- 3/3  ярлыки на общем рабочем столе ---'
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'bin\install-shortcuts.ps1') 2>&1 |
  ForEach-Object { Add-Content -Path $log -Value $_ -Encoding UTF8 }

Say ''
Say '=== ГОТОВО ==='
Say "лог: $log"

if (-not $SkipHermes) {
  $exe = 'C:\Users\finni\AppData\Local\hermes\bin\hermes.exe'
  $tools = 'C:\Users\finni\AppData\Local\hermes\tools'
  $empty = @()
  if (Test-Path $tools) {
    Get-ChildItem $tools -Directory -ErrorAction SilentlyContinue | ForEach-Object {
      $n = @(Get-ChildItem $_.FullName -Force -ErrorAction SilentlyContinue).Count
      if ($n -eq 0) { $empty += $_.Name }
    }
  }
  if ($empty.Count -eq 0) {
    Say 'тулчейн Hermes восстановлен.'
  } else {
    Say "ВНИМАНИЕ: осталось пустых папок: $($empty.Count) ($($empty -join ', '))"
  }
}

Write-Host ''
Read-Host 'Нажмите Enter, чтобы закрыть это окно'
