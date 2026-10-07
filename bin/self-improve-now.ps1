param([int]$Changes = 1, [switch]$NoPause)
$ErrorActionPreference = 'Continue'

# Ручной запуск цикла самоулучшения с видимым выводом.
$root = 'C:\Users\finni\agent-system'
$py = 'C:\Users\finni\AppData\Local\Programs\Python\Python312\python.exe'

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try { chcp 65001 | Out-Null } catch {}

$host.UI.RawUI.WindowTitle = 'ALEX - самоулучшение'
$env:PYTHONIOENCODING = 'utf-8'

Set-Location $root
Write-Host ''
Write-Host '  Цикл самоулучшения' -ForegroundColor Cyan
Write-Host '  ------------------'
Write-Host '  порядок: замер до -> поиск слабого места -> одна правка -> замер после'
Write-Host '  нейтральные и ухудшившие изменения откатываются автоматически'
Write-Host ''

if (-not (Test-Path $py)) { Write-Host "нет интерпретатора: $py" -ForegroundColor Red; exit 1 }

$llm = $null
try {
    $r = Invoke-WebRequest 'http://127.0.0.1:8080/health' -UseBasicParsing -TimeoutSec 5
    $llm = ($r.Content -match 'ok')
} catch { $llm = $false }

if (-not $llm) {
    Write-Host '  LLM-сервер не отвечает. Поднимаю...' -ForegroundColor Yellow
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'bin\start-runtime.ps1')
}

& $py (Join-Path $root 'agent\self_improve.py') $Changes
$code = $LASTEXITCODE

Write-Host ''
if ($code -eq 0) {
    Write-Host '  Цикл завершён.' -ForegroundColor Green
} else {
    Write-Host "  Цикл завершился с кодом $code" -ForegroundColor Yellow
}

Write-Host '  Что изменилось:'
& $py (Join-Path $root 'agent\version_agent.py') status

if (-not $NoPause) {
    Write-Host ''
    Read-Host '  Нажмите Enter'
}
