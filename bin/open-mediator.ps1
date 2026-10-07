$ErrorActionPreference = 'Continue'

# Посредник: запускает Hermes с ролью посредника в системном промпте сессии.
# Механизм - HERMES_EPHEMERAL_SYSTEM_PROMPT: личность действует только в этой
# сессии и не трогает глобальный конфиг, поэтому обычный Hermes остаётся
# обычным.
$root = 'C:\Users\finni\agent-system'
$hermesBin = 'C:\Users\finni\AppData\Local\hermes\bin'
$identityFile = Join-Path $root 'hermes-skills\alex\alex-mediator\IDENTITY.md'

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try { chcp 65001 | Out-Null } catch {}

$host.UI.RawUI.WindowTitle = 'ALEX - посредник'

if (-not (Test-Path (Join-Path $hermesBin 'hermes.exe'))) {
    Write-Host "Hermes не найден: $hermesBin" -ForegroundColor Red
    Read-Host 'Enter'
    exit 1
}

if (-not (Test-Path $identityFile)) {
    Write-Host "нет файла личности: $identityFile" -ForegroundColor Red
    Read-Host 'Enter'
    exit 1
}

# Читаем личность из файла и передаём её как системный промпт сессии.
$identity = Get-Content -LiteralPath $identityFile -Raw -Encoding UTF8
if ([string]::IsNullOrWhiteSpace($identity)) {
    Write-Host 'файл личности пуст' -ForegroundColor Red
    Read-Host 'Enter'
    exit 1
}

$env:HERMES_EPHEMERAL_SYSTEM_PROMPT = $identity
$env:Path = "$hermesBin;$root\bin;$env:Path"
$env:PYTHONIOENCODING = 'utf-8'

$llmOk = $false
try {
    $r = Invoke-WebRequest 'http://127.0.0.1:8080/health' -UseBasicParsing -TimeoutSec 5
    $llmOk = ($r.Content -match 'ok')
} catch { $llmOk = $false }

if (-not $llmOk) {
    Write-Host '  LLM-сервер не отвечает, поднимаю...' -ForegroundColor Yellow
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'bin\start-runtime.ps1')
}

Set-Location $root

$open = & (Join-Path $root 'bin\alex.cmd') questions --status open 2>$null | Select-Object -First 1
Write-Host ''
Write-Host '  ПОСРЕДНИК ALEX' -ForegroundColor Cyan
Write-Host '  --------------'
Write-Host '  задачи ставишь ты, состояние и очередь вопросов - тоже ты'
Write-Host "  модель: $(if ($llmOk) { 'работает' } else { 'не отвечает' })"
if ($open) { Write-Host "  очередь: $open" -ForegroundColor DarkGray }
Write-Host ''
Write-Host '  Начни с: сколько вопросов накопилось?' -ForegroundColor DarkGray
Write-Host ''

& (Join-Path $hermesBin 'hermes.exe')

Write-Host ''
Write-Host '  Посредник завершён.' -ForegroundColor DarkGray
Start-Sleep -Seconds 4
