$ErrorActionPreference = 'Continue'

# Рабочий режим: поднимает LLM-сервер, агентную систему и автозапуск при старте Windows.
$root = 'C:\Users\finni\agent-system'
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Test-IsAdmin {
  ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
}

Write-Host ''
Write-Host '  === Рабочий режим ===' -ForegroundColor Cyan

# 1. Автозапуск LLM-сервера и цикла самоулучшения
$reg = Join-Path $root 'bin\register-autostart.ps1'
if (Test-Path $reg) {
  if (Test-IsAdmin) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $reg -Mode enable
  } else {
    Write-Host '  поднимаю права для автозапуска...' -ForegroundColor DarkGray
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden -ArgumentList @(
      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$reg`"", '-Mode', 'enable'
    )
  }
}

# 2. LLM-сервер
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'bin\start-runtime.ps1')

# 3. Индекс RAG
& python (Join-Path $root 'rag\index.py') | Out-Null

# 4. Первая задача: система улучшает саму себя
Write-Host ''
Write-Host '  запускаю первый цикл самоулучшения...' -ForegroundColor Cyan
& python (Join-Path $root 'agent\self_improve.py') 1

# 5. Отчёт
& python (Join-Path $root 'agent\report.py')

Write-Host ''
Write-Host '  Рабочий режим включён.' -ForegroundColor Green
Write-Host '  ИИ занимает всю VRAM - для игр переключитесь на "Игровой режим".' -ForegroundColor Yellow
Write-Host ''
Read-Host '  Нажмите Enter'
