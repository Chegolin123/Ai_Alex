$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try { chcp 65001 | Out-Null } catch {}

$host.UI.RawUI.WindowTitle = 'Hermes Agent - Ornith-1.5-9B (localhost:8080)'

$hermesBin = 'C:\Users\finni\AppData\Local\hermes\bin'
if (-not (Test-Path (Join-Path $hermesBin 'hermes.exe'))) {
    Write-Host "Hermes не найден: $hermesBin" -ForegroundColor Red
    Read-Host 'Enter'
    exit 1
}

$env:Path = "$hermesBin;$env:Path"
$env:PYTHONIOENCODING = 'utf-8'

$serverUp = $false
try {
    $r = Invoke-WebRequest 'http://127.0.0.1:8080/health' -UseBasicParsing -TimeoutSec 5
    $serverUp = ($r.Content -match 'ok')
} catch { $serverUp = $false }

Write-Host ''
Write-Host '  Hermes Agent' -ForegroundColor Cyan
Write-Host '  ------------'
Write-Host "  модель       : Ornith-1.5-9B"
Write-Host '  endpoint     : http://127.0.0.1:8080/v1'
Write-Host "  сервер       : $(if ($serverUp) { 'работает' } else { 'НЕ ОТВЕЧАЕТ - запусти start-runtime.ps1' })"
Write-Host "  рабочая папка: C:\Users\finni\agent-system"
Write-Host '  доступ       : полный (approvals.mode=off)'
Write-Host ''
Write-Host '  /help - список команд, /exit - выход, Ctrl+C - прервать' -ForegroundColor DarkGray
Write-Host ''

Set-Location 'C:\Users\finni\agent-system'
& (Join-Path $hermesBin 'hermes.exe')

Write-Host ''
Write-Host '  Hermes завершён.' -ForegroundColor DarkGray
Start-Sleep -Seconds 5
