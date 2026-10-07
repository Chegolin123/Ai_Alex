param(
  [ValidateSet('enable', 'disable', 'status')]
  [string]$Mode = 'status'
)
$ErrorActionPreference = 'Continue'

# Регистрация задач планировщика для агентной системы.
# Требует прав администратора.
$root    = 'C:\Users\finni\agent-system'
$py      = 'C:\Users\finni\AppData\Local\Programs\Python\Python312\python.exe'
$logDir  = Join-Path $root 'logs'

$tasks = @(
  @{
    Name     = 'agent-improve-hourly'
    Action   = "`"$py`" `"$root\agent\self_improve.py`" 1 >> `"$logDir\improve-hourly.log`" 2>&1"
    # RepetitionInterval без RepetitionDuration не работает: Windows считает такой
    # триггер однократным и задача срабатывает ровно один раз. Длительность в
    # 10 лет даёт реальный hourly до 2036 года.
    Trigger  = (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(3) `
                -RepetitionInterval (New-TimeSpan -Hours 1) `
                -RepetitionDuration (New-TimeSpan -Days 3650))
    RunLevel = 'Limited'
    Desc     = 'Ежечасный цикл самоулучшения агентной системы'
  }
  @{
    Name     = 'agent-runtime-startup'
    Action   = "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\bin\start-runtime.ps1`""
    Trigger  = (New-ScheduledTaskTrigger -AtStartup)
    RunLevel = 'Limited'
    Desc     = 'Поднимает llama-server при старте Windows (рабочий режим)'
  }
  @{
    Name     = 'agent-metrics-daily'
    Action   = "`"$py`" `"$root\agent\metrics.py`" >> `"$logDir\metrics-daily.log`" 2>&1"
    Trigger  = (New-ScheduledTaskTrigger -Daily -At '11:00')
    RunLevel = 'Limited'
    Desc     = 'Сбор метрик и переиндексация RAG раз в сутки'
  }
  @{
    Name     = 'agent-version-daily'
    Action   = "`"$py`" `"$root\agent\version_agent.py`" snapshot --bump patch --push >> `"$logDir\version-daily.log`" 2>&1"
    # Повторение задаётся явно вместе с длительностью: без -RepetitionDuration
    # Windows не считает триггер повторяющимся и задача срабатывает один раз.
    Trigger  = (New-ScheduledTaskTrigger -Daily -At '23:30')
    RunLevel = 'Limited'
    Desc     = 'Ежедневный снимок версии с отчётом и отправкой в GitHub'
  }
)

function Test-IsAdmin {
  ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Show-Status {
  foreach ($t in $tasks) {
    $task = Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
    if ($task) {
      Write-Host ("  {0,-26} {1,-9} {2}" -f $t.Name, $task.State, $t.Desc) -ForegroundColor Gray
    } else {
      Write-Host ("  {0,-26} {1,-9} {2}" -f $t.Name, 'нет', $t.Desc) -ForegroundColor DarkGray
    }
  }
}

if ($Mode -eq 'status') {
  Write-Host '  Задачи агентной системы:' -ForegroundColor Cyan
  Show-Status
  exit 0
}

if (-not (Test-IsAdmin)) {
  Write-Host 'нужны права администратора - открываю UAC...' -ForegroundColor Cyan
  Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Mode', $Mode
  )
  exit 0
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

foreach ($t in $tasks) {
  switch ($Mode) {
    'enable' {
      $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel $t.RunLevel
      Register-ScheduledTask -TaskName $t.Name `
        -Action (New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c $($t.Action)") `
        -Trigger $t.Trigger -Principal $principal `
        -Settings (New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
                     -ExecutionTimeLimit (New-TimeSpan -Hours 2) -StartWhenAvailable) `
        -Description $t.Desc -Force | Out-Null
      Enable-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue | Out-Null
      Write-Host "  включена: $($t.Name)" -ForegroundColor Green
    }
    'disable' {
      Disable-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue | Out-Null
      Stop-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue | Out-Null
      Write-Host "  отключена: $($t.Name)" -ForegroundColor Yellow
    }
  }
}

Write-Host ''
Write-Host "  Задачи ($Mode):" -ForegroundColor Cyan
Show-Status
