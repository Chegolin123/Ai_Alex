$ErrorActionPreference = 'Continue'

# Игровой режим: останавливает LLM-сервер и цикл агента, отдаёт всю VRAM играм.
$root = 'C:\Users\finni\agent-system'

function Test-IsAdmin {
  ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-VramUsed {
  $v = & nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>$null | Select-Object -First 1
  if ($v) { [int]$v } else { $null }
}

Write-Host ''
Write-Host '  === Игровой режим ===' -ForegroundColor Cyan

# 1. Цикл агента
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match 'agent-system' } |
  ForEach-Object {
    Write-Host "  останавливаю агент (PID $($_.ProcessId))"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }

# 2. Автозапуск
$reg = Join-Path $root 'bin\register-autostart.ps1'
if (Test-Path $reg) {
  if (Test-IsAdmin) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $reg -Mode disable
  } else {
    Write-Host '  поднимаю права для отключения автозапуска...' -ForegroundColor DarkGray
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden -ArgumentList @(
      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$reg`"", '-Mode', 'disable'
    )
  }
}

# 3. LLM-сервер (свои задачи планировщика)
foreach ($name in @('llama-server', 'bonsai-server')) {
  $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if ($t) { Disable-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue | Out-Null }
}
Get-Process llama-server -ErrorAction SilentlyContinue | ForEach-Object {
  Write-Host "  останавливаю llama-server (PID $($_.Id))"
  Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 5

$vram = Get-VramUsed
Write-Host ''
if ($null -ne $vram) {
  Write-Host ("  VRAM занято: {0} МБ из 8192" -f $vram) -ForegroundColor Green
  if ($vram -lt 1500) { Write-Host '  Видеокарта свободна - можно играть.' -ForegroundColor Green }
  else { Write-Host '  Если память не освободилась - закройте окна, которым она нужна.' -ForegroundColor Yellow }
}
Write-Host '  Автозапуск ИИ отключён: после перезагрузки сервер не поднимется.' -ForegroundColor Yellow
Write-Host '  Вернуть ИИ - ярлык "Рабочий режим".' -ForegroundColor DarkGray
Write-Host ''
Read-Host '  Нажмите Enter'
