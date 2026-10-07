$ErrorActionPreference = 'Continue'

# Ремонт тулчейна Hermes: папки portable-тулов (python/node/git/uv/...) пустые,
# хотя facts.json их знает. Инсталлятор проверяет SHA256 каждого пакета и
# перекачивает недостающее, поэтому достаточно повторного прогона.
$dir  = 'C:\Users\finni\hermes-install'
$inst = Join-Path $dir 'install.ps1'
$log  = Join-Path $dir 'repair.log'

if (-not (Test-Path $inst)) { Write-Host "нет $inst" -ForegroundColor Red; exit 1 }

function Test-IsAdmin {
  ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
}

$toolDirs = @(
  'C:\Users\finni\AppData\Local\hermes\tools\python-3.14.7+20260901-win32-x64'
  'C:\Users\finni\AppData\Local\hermes\tools\node-26.7.0-win32-x64'
  'C:\Users\finni\AppData\Local\hermes\tools\git-2.53.0+3-win32-x64'
  'C:\Users\finni\AppData\Local\hermes\tools\uv-0.12.3-win32-x64'
)

$hermesRoot = 'C:\Users\finni\AppData\Local\hermes'
$hermesUser = "$env:USERDOMAIN\$env:USERNAME"
$hermesPython = Join-Path $hermesRoot 'tools\python-3.14.7+20260901-win32-x64\python.exe'

function Test-ToolsOk {
  foreach ($d in $toolDirs) {
    $n = (Get-ChildItem $d -Recurse -Force -EA SilentlyContinue | Measure-Object).Count
    if ($n -eq 0) { return $false }
  }
  return $true
}

function Test-UserAccess {
  try {
    $v = & $hermesPython --version 2>&1 | Out-String
    return ($LASTEXITCODE -eq 0 -and $v -match 'Python')
  } catch { return $false }
}

function Grant-UserAccess {
  # Installing with elevation leaves the toolchain readable by admins only, and
  # then hermes.exe fails with exit 1 and no output for the interactive user.
  # That was the real cause of the "empty toolchain" symptom here.
  $tools = Join-Path $hermesRoot 'tools'
  foreach ($p in @($tools, (Join-Path $hermesRoot 'bin'))) {
    if (-not (Test-Path $p)) { continue }
    try {
      $acl = Get-Acl $p
      $acl.SetAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        $hermesUser, 'ReadAndExecute', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
      Set-Acl -Path $p -AclObject $acl
    } catch { Write-Host "  не удалось выдать права на ${p}: $($_.Exception.Message)" -ForegroundColor Yellow }
  }
  & icacls $tools /grant "${hermesUser}:(OI)(CI)(RX)" /T /C /Q | Out-Null
  Write-Host "  права на чтение/выполнение выданы: $hermesUser" -ForegroundColor Green
}

if ((Test-ToolsOk) -and (Test-UserAccess)) {
  Write-Host 'тулчейн цел и доступен текущему пользователю - ремонт не нужен' -ForegroundColor Green
  exit 0
}

if (-not (Test-IsAdmin)) {
  Write-Host 'ремонт требует прав администратора - открываю UAC...' -ForegroundColor Cyan
  Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`""
  )
  # починено с правами админа, но тулчейн мог остаться недоступным пользователю
  Grant-UserAccess
  if (Test-UserAccess) {
    Write-Host 'тулчейн восстановлен и доступен' -ForegroundColor Green
    $env:Path = "C:\Users\finni\AppData\Local\hermes\bin;$env:Path"
    hermes --version
  } else {
    Write-Host 'после ремонта доступ всё ещё закрыт - смотри repair.log' -ForegroundColor Red
    Get-Content $log -Tail 20 -EA SilentlyContinue
  }
  exit 0
}

Write-Host 'запускаю инсталлятор с проверкой контрольных сумм...' -ForegroundColor Cyan
& powershell -NoProfile -ExecutionPolicy Bypass -File $inst -NonInteractive *>&1 | Tee-Object -FilePath $log

Grant-UserAccess

if (Test-UserAccess) {
  Write-Host ''
  Write-Host 'тулчейн восстановлен и доступен текущему пользователю' -ForegroundColor Green
  $env:Path = "C:\Users\finni\AppData\Local\hermes\bin;$env:Path"
  hermes --version
} else {
  Write-Host ''
  Write-Host 'тулчейн НЕ восстановлен' -ForegroundColor Red
  Get-Content $log -Tail 30 -EA SilentlyContinue
  exit 1
}
