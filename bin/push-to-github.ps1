$ErrorActionPreference = 'Continue'
$log = 'C:\Users\finni\agent-system\logs\push.log'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function Say($m) { $l = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m; Add-Content -LiteralPath $log -Value $l -Encoding UTF8; Write-Host $l }

"" | Set-Content -LiteralPath $log -Encoding UTF8
Set-Location 'C:\Users\finni\agent-system'
$env:PYTHONIOENCODING = 'utf-8'

Say 'отправка в https://github.com/Chegolin123/Ai_Alex'
Say 'если появится окно GitHub - войди в аккаунт Chegolin123'

python 'C:\Users\finni\agent-system\agent\version_agent.py' push *>&1 | ForEach-Object { Say $_ }

$code = $LASTEXITCODE
Say "код возврата: $code"
Say 'ГОТОВО'
