$ErrorActionPreference = 'Continue'

# Поднимает llama-server с расчётом бюджета VRAM и проверкой полного оффлоада.
# Модель Ornith-1.5-9B-Q4_K_M (5.77 GB) целиком помещается в 8 GB карту.
# Бюджет VRAM (МБ):
#   веса 5769 + KV-кэш + compute-буферы (~250) <= свободная VRAM
#   KV: q8_0 ~6.5 KB/токен, q4_0 ~3.3 KB/токен
$root = 'D:\llm'
$Port = 8080
# Ключ берётся из config.json агентной системы, который не отслеживается git.
# Дублировать его здесь нельзя: файл попадает в публичный репозиторий.
$cfgPath = 'C:\Users\finni\agent-system\config.json'
if (Test-Path $cfgPath) {
  $Key = (Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json).llm.api_key
} else {
  $Key = $env:AGENT_API_KEY
}
if (-not $Key) {
  Write-Host 'нет API-ключа: проверь config.json или переменную AGENT_API_KEY' -ForegroundColor Red
  exit 1
}

$WeightsMb  = 5769
$BuffersMb  = 300
$KvKbPerTok = @{ q8_0 = 6.5; q4_0 = 3.3; f16 = 13.0 }
$SafetyMb   = 150

function Get-Vram { & nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>$null | Select-Object -First 1 }

function Wait-Api([int]$seconds) {
  $end = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $end) {
    try {
      $null = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/models" `
              -Headers @{ Authorization = "Bearer $Key" } -TimeoutSec 5
      return $true
    } catch { Start-Sleep -Seconds 5 }
  }
  return $false
}

if (Get-NetTCPConnection -LocalPort $Port -State Listen -EA SilentlyContinue) {
  Write-Host "порт $Port уже занят" -ForegroundColor Yellow
  exit 0
}

# Кандидаты от лучшего к худшему: сначала максимум контекста при q8_0, затем q4_0.
$candidates = @(
  @{ ctx = 131072; kv = 'q8_0' }
  @{ ctx = 131072; kv = 'q4_0' }
  @{ ctx =  98304; kv = 'q8_0' }
  @{ ctx =  65536; kv = 'q8_0' }
)

$free = [int](Get-Vram)
Write-Host "свободно VRAM: $free МБ"

$pick = $null
foreach ($c in $candidates) {
  $need = $WeightsMb + [math]::Ceiling($c.ctx * $KvKbPerTok[$c.kv] / 1024) + $BuffersMb + $SafetyMb
  $fit = $free -ge $need
  Write-Host ("  {0,7} ctx / {1} KV -> нужно ~{2} МБ  {3}" -f $c.ctx, $c.kv, $need, $(if ($fit) { 'ВЛЕЗАЕТ' } else { 'не влезает' }))
  if ($fit -and -not $pick) { $pick = $c }
}

if (-not $pick) {
  Write-Host 'VRAM не хватает ни для одной конфигурации. Закройте игры/приложения и повторите.' -ForegroundColor Red
  exit 1
}

Write-Host ("запускаю: ctx=$($pick.ctx), KV=$($pick.kv)") -ForegroundColor Cyan
& powershell -NoProfile -ExecutionPolicy Bypass -File "$root\run-server.ps1" -Context $pick.ctx -KvType $pick.kv

if (-not (Wait-Api 240)) {
  Write-Host 'сервер не ответил, лог:' -ForegroundColor Red
  Get-Content "$root\logs\server.err.log" -EA SilentlyContinue | Select-Object -Last 25
  exit 1
}

# --- верификация ---------------------------------------------------------
$props = Invoke-RestMethod "http://127.0.0.1:$Port/props" -Headers @{ Authorization = "Bearer $Key" }
$nCtx = $props.default_generation_settings.n_ctx

$body = @{
  model = 'Ornith-1.5-9B'
  messages = @(@{ role = 'user'; content = 'Reply with exactly: READY' })
  max_tokens = 24
  temperature = 0.1
} | ConvertTo-Json -Depth 5
$sw = [Diagnostics.Stopwatch]::StartNew()
Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/chat/completions" -Method Post `
  -Headers @{ Authorization = "Bearer $Key" } -ContentType 'application/json' -Body $body | Out-Null
$sw.Stop()

$used = (& nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | Select-Object -First 1)
$freeAfter = (& nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | Select-Object -First 1)
$temp = (& nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader | Select-Object -First 1)

Write-Host ''
Write-Host 'ГОТОВО' -ForegroundColor Green
Write-Host "  контекст      : $nCtx (ожидался $($pick.ctx))"
Write-Host "  KV-кэш        : $($pick.kv)"
Write-Host "  VRAM занято   : $used МБ, свободно $freeAfter МБ"
Write-Host "  температура   : $temp C"
Write-Host "  первый запрос : $([math]::Round($sw.Elapsed.TotalSeconds,1)) с"

if ($nCtx -ne $pick.ctx) { Write-Host '  ВНИМАНИЕ: контекст не совпал с планом' -ForegroundColor Yellow }

# Признак частичного оффлоада - модель уходит в RAM, скорость падает в разы.
$timings = Get-Content "$root\logs\server.err.log" -Tail 6 -Encoding UTF8 -EA SilentlyContinue |
           Where-Object { $_ -match 'eval time' } | Select-Object -Last 1
if ($timings -and $timings -match '([\d\.]+) tokens per second') {
  $tps = [double]$Matches[1]
  Write-Host ("  скорость      : {0} tok/s" -f $tps)
  if ($tps -lt 20) { Write-Host '  ВНИМАНИЕ: скорость низкая, возможен spill в CPU' -ForegroundColor Yellow }
} else {
  Write-Host '  скорость      : нет данных в логе'
}

exit 0
