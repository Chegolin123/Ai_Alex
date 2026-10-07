param([string]$Task = 'agent-improve-hourly', [int]$WaitSec = 240)
$ErrorActionPreference = 'SilentlyContinue'
$log = 'C:\Users\finni\agent-system\logs\taskcheck.txt'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function Say($m) { $l = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m; Add-Content -LiteralPath $log -Value $l -Encoding UTF8 }

"" | Set-Content -LiteralPath $log -Encoding UTF8

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
Say "админ=$isAdmin"

if (-not $isAdmin) {
  Say 'прав нет, запрашиваю UAC и перезапускаю себя'
  Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Task', $Task, '-WaitSec', $WaitSec)
  exit 0
}

$watched = @(
  'C:\Users\finni\agent-system\logs\improve-hourly.log'
  'C:\Users\finni\agent-system\logs\version-daily.log'
  'C:\Users\finni\agent-system\logs\metrics-daily.log'
)
foreach ($f in $watched) {
  Say ("{0}: {1}" -f (Split-Path $f -Leaf), $(if (Test-Path $f) { (Get-Item $f).Length } else { 'нет файла' }))
}

Say ''
Say "триггеры задачи $Task :"
$t = Get-ScheduledTask -TaskName $Task
$t.Triggers | ForEach-Object {
  Say ("  start={0} interval={1} duration={2} enabled={3}" -f `
    $_.StartBoundary, $_.Repetition.Interval, $_.Repetition.Duration, $_.Enabled)
}
$info = Get-ScheduledTaskInfo -TaskName $Task
Say ("  lastRun={0} lastResult=0x{1:X} nextRun={2} runs={3}" -f `
  $info.LastRunTime, $info.LastTaskResult, $info.NextRunTime, $info.NumberOfRunsSucceeded)
Say ("  missedRuns={0}" -f $info.NumberOfRunsMissed)

Say ''
Say "запускаю $Task вручную..."
Start-ScheduledTask -TaskName $Task

$deadline = (Get-Date).AddSeconds($WaitSec)
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Seconds 10
  $i2 = Get-ScheduledTaskInfo -TaskName $Task
  if ($i2.LastRunTime -gt $info.LastRunTime) {
    Say ("  запущена в {0}" -f $i2.LastRunTime)
    break
  }
}
Start-Sleep -Seconds 20
$after = Get-ScheduledTaskInfo -TaskName $Task
Say ("  после: lastRun={0} lastResult=0x{1:X} state={2}" -f `
  $after.LastRunTime, $after.LastTaskResult, (Get-ScheduledTask -TaskName $Task).State)

Say ''
Say 'файлы логов сейчас:'
foreach ($f in $watched) {
  if (Test-Path $f) {
    Say ("  {0} = {1} байт, изменён {2}" -f (Split-Path $f -Leaf), (Get-Item $f).Length, (Get-Item $f).LastWriteTime)
  } else {
    Say ("  {0} = нет файла" -f (Split-Path $f -Leaf))
  }
}
Say 'ГОТОВО'
