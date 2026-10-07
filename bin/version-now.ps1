param([switch]$NoPause)
$ErrorActionPreference = 'Continue'

# Ручной снимок версии: отчёт, тег, отправка в GitHub.
$root = 'C:\Users\finni\agent-system'
$py = 'C:\Users\finni\AppData\Local\Programs\Python\Python312\python.exe'

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try { chcp 65001 | Out-Null } catch {}

$host.UI.RawUI.WindowTitle = 'ALEX - версия и отчет'
$env:PYTHONIOENCODING = 'utf-8'
$env:GIT_TERMINAL_PROMPT = '0'

Set-Location $root
Write-Host ''
Write-Host '  Снимок версии и отчёт' -ForegroundColor Cyan
Write-Host '  ---------------------'
Write-Host ''

if (-not (Test-Path $py)) { Write-Host "нет интерпретатора: $py" -ForegroundColor Red; exit 1 }

& $py (Join-Path $root 'agent\version_agent.py') snapshot --bump patch --push
$code = $LASTEXITCODE

Write-Host ''
Write-Host '  История версий:'
& $py (Join-Path $root 'agent\version_agent.py') history

Write-Host ''
Write-Host '  Последний отчёт:'
$last = Get-ChildItem (Join-Path $root 'reports') -Filter '*.md' -EA SilentlyContinue |
       Sort-Object Name | Select-Object -Last 1
if ($last) {
    Write-Host "    $($last.FullName)"
    Get-Content $last.FullName -Encoding UTF8 | Select-Object -First 22
} else {
    Write-Host '    отчётов пока нет'
}

if (-not $NoPause) {
    Write-Host ''
    Read-Host '  Нажмите Enter'
}
