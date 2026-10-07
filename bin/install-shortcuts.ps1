$ErrorActionPreference = 'Stop'

# Кладёт ярлыки рабочего и игрового режима на рабочий стол (текущего и общий).
$root = 'C:\Users\finni\agent-system\bin'
$desktops = @(
  [Environment]::GetFolderPath('Desktop')
  'C:\Users\Public\Desktop'
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique

# Filenames may be Cyrillic - the filesystem is Unicode. The .cmd CONTENT must be
# pure ASCII: cmd.exe reads a batch file in the OEM codepage, so a Cyrillic byte
# in it corrupts the following line and turns `echo` into `ho`. That is why the
# titles below are ASCII even though the shortcut names are not.
$items = @(
  @{ Name = 'Посредник.cmd'; Script = 'open-mediator.ps1'; Title = 'ALEX - Mediator' }
  @{ Name = 'Hermes Agent.cmd'; Script = 'open-hermes.ps1'; Title = 'Hermes Agent' }
  @{ Name = 'Рабочий режим.cmd'; Script = 'work-mode.ps1'; Title = 'ALEX - Work Mode' }
  @{ Name = 'Игровой режим.cmd'; Script = 'game-mode.ps1'; Title = 'ALEX - Game Mode' }
  @{ Name = 'Самоулучшение.cmd'; Script = 'self-improve-now.ps1'; Title = 'ALEX - Self Improvement' }
  @{ Name = 'Самоулучшение (размышления).cmd'; Script = 'self-improve-now.ps1';
     Title = 'ALEX - Self Improvement (thinking)'; Args = '-Thinking' }
  @{ Name = 'Версия и отчёт.cmd'; Script = 'version-now.ps1'; Title = 'ALEX - Version Snapshot' }
)

$ok = 0; $skipped = @()
foreach ($d in $desktops) {
  foreach ($i in $items) {
    $target = Join-Path $root $i.Script
    if (-not (Test-Path $target)) {
      Write-Host "нет $target" -ForegroundColor Red
      continue
    }
    $body = @(
      '@echo off'
      "title $($i.Title)"
      "powershell -NoProfile -ExecutionPolicy Bypass -File `"$target`" $($i.Args)"
      'timeout /t 3 >nul'
    )
    if (($body -join "`n") -match '[^\x00-\x7F]') {
      throw "в содержимом $target осталась не-ASCII - cmd.exe его сломает"
    }
    $path = Join-Path $d $i.Name
    # The public desktop needs elevation. Report and continue instead of aborting,
    # otherwise nothing after the first failure is created anywhere.
    try {
      [System.IO.File]::WriteAllLines($path, $body, (New-Object System.Text.ASCIIEncoding))
      Write-Output "создан: $path"
      $ok++
    } catch {
      $skipped += $path
      Write-Host "нужны права администратора: $path" -ForegroundColor Yellow
    }
  }
}

Write-Output ''
Write-Output "создано: $ok"
if ($skipped.Count) {
  Write-Output ''
  Write-Host 'Чтобы создать ярлыки на общем рабочем столе, запусти от администратора:' -ForegroundColor Yellow
  Write-Host '  powershell -ExecutionPolicy Bypass -File "C:\Users\finni\agent-system\bin\install-shortcuts.ps1"' -ForegroundColor Yellow
}

