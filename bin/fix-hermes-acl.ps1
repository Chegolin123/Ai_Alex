$ErrorActionPreference = 'Continue'
$log = 'C:\Users\finni\agent-system\logs\aclfix.txt'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function Say($m) { $l = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m; Write-Host $l; Add-Content -LiteralPath $log -Value $l -Encoding UTF8 }

"" | Set-Content -LiteralPath $log -Encoding UTF8

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
$targetUser = 'ALEX\finni'

Say "=== ПОЧИНИТЬ ПРАВА HERMES ==="
Say "админ=$isAdmin пользователь=$($id.Name)"

if (-not $isAdmin) {
  Say 'прав нет, запрашиваю UAC'
  Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
  exit 0
}

$hermesHome = 'C:\Users\finni\AppData\Local\hermes'

Say ''
Say '--- 1. что сейчас в тулчейне (видно с правами админа) ---'
$tools = Join-Path $hermesHome 'tools'
$empty = @(); $full = @()
foreach ($d in @(Get-ChildItem $tools -Directory -ErrorAction SilentlyContinue)) {
  $n = @(Get-ChildItem $d.FullName -Force -ErrorAction SilentlyContinue).Count
  if ($n -eq 0) { $empty += $d.Name } else { $full += $d.Name }
}
Say "  заполнено: $($full.Count), пусто: $($empty.Count)"
if ($full.Count) { Say "  заполнены: $($full -join ', ')" }

Say ''
Say '--- 2. владельцы и права ---'
Say "  tools    : $((Get-Acl $tools).Owner)"
$toolsAcl = Get-Acl $tools
$toolsAcl.Access | ForEach-Object { Say ("    {0,-14} {1,-12} {2}" -f $_.IdentityReference, $_.FileSystemRights, $_.AccessControlType) }

Say ''
Say '--- 3. выдаю пользователю чтение и выполнение рекурсивно ---'
$inherit = 'ContainerInherit,ObjectInherit'
foreach ($path in @($tools, (Join-Path $hermesHome 'bin'))) {
  if (-not (Test-Path $path)) { continue }
  try {
    $acl = Get-Acl $path
    foreach ($r in @(
      (New-Object System.Security.AccessControl.FileSystemAccessRule($targetUser, 'ReadAndExecute', $inherit, 'None', 'Allow')),
      (New-Object System.Security.AccessControl.FileSystemAccessRule($targetUser, 'ReadAndExecute', 'None', 'None', 'Allow'))
    )) { $acl.SetAccessRule($r) }
    Set-Acl -Path $path -AclObject $acl
    Say "  права выданы: $path"
  } catch {
    Say "  ОШИБКА на $path : $($_.Exception.Message)"
  }
}
# уже созданные вложенные объекты
try {
  & icacls $tools /grant "${targetUser}:(OI)(CI)(RX)" /T /C /Q | Out-Null
  Say "  icacls /T применён к $tools"
} catch { Say "  icacls: $($_.Exception.Message)" }

Say ''
Say '--- 4. проверка доступа на чтение ---'
$py = Join-Path $tools 'python-3.14.7+20260901-win32-x64\python.exe'
Say "  python.exe: $(Test-Path $py)"
if (Test-Path $py) {
  $v = & $py --version 2>&1 | Out-String
  Say "  версия: $($v.Trim())"
}

Say ''
Say 'ГОТОВО'
Read-Host 'Нажмите Enter'

