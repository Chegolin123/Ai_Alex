$ErrorActionPreference = 'Continue'
$log = 'C:\Users\finni\agent-system\logs\verify.txt'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function Say($m) { $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m; Write-Host $line; Add-Content -LiteralPath $log -Value $line -Encoding UTF8 }

"" | Set-Content -LiteralPath $log -Encoding UTF8

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
Say "запуск. админ=$isAdmin пользователь=$($id.Name)"

$tools = 'C:\Users\finni\AppData\Local\hermes\tools'
$user  = "$env:USERDOMAIN\$env:USERNAME"

Say ''
Say '--- 1. scheduled tasks (agent-*) ---'
$tasks = @(Get-ScheduledTask | Where-Object { $_.TaskName -like 'agent-*' })
if ($tasks.Count -gt 0) {
  foreach ($t in $tasks) { Say ("  {0,-26} state={1,-8} enabled={2}" -f $t.TaskName, $t.State, $t.Settings.Enabled) }
} else {
  Say '  НЕ НАЙДЕНЫ'
}

Say ''
Say '--- 2. тулчейн Hermes ---'
$empty = @(); $full = @()
foreach ($d in @(Get-ChildItem $tools -Directory -ErrorAction SilentlyContinue)) {
  $n = @(Get-ChildItem $d.FullName -Force -ErrorAction SilentlyContinue).Count
  if ($n -eq 0) { $empty += $d.Name } else { $full += $d.Name }
}
Say "  заполнено: $($full.Count), пусто: $($empty.Count)"
if ($empty.Count) { Say "  пустые: $($empty -join ', ')" }
Say "  владелец tools: $((Get-Acl $tools).Owner)"

Say ''
Say '--- 3. права обычного пользователя ---'
$py = Join-Path $tools 'python-3.14.7+20260901-win32-x64\python.exe'
Say "  python.exe на диске: $(Test-Path $py)"
if (-not $isAdmin) {
  Say '  (этот скрипт запущен без прав - чтение папок может быть запрещено)'
}

Say ''
Say '--- 4. выдача прав пользователю и повторная проверка ---'
if (-not $isAdmin) {
  Say '  нет прав администратора, выдать доступ не могу'
} else {
  $acl = Get-Acl $tools
  $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $user, 'ReadAndExecute', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
  $acl.SetAccessRule($rule)
  Set-Acl -Path $tools -AclObject $acl
  Say "  доступ на чтение/выполнение выдан: $user"

  $empty2 = @()
  foreach ($d in @(Get-ChildItem $tools -Directory)) {
    if (@(Get-ChildItem $d.FullName -Force -ErrorAction SilentlyContinue).Count -eq 0) { $empty2 += $d.Name }
  }
  Say "  после выдачи прав пустых папок: $($empty2.Count)"

  Say ''
  Say '--- 5. hermes CLI от админа ---'
  $env:Path = "C:\Users\finni\AppData\Local\hermes\bin;$env:Path"
  $v = & hermes --version 2>&1 | Out-String
  Say ($v.Trim())
}

Say ''
Say 'ГОТОВО'
