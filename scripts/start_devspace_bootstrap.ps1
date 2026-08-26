[CmdletBinding()]
param(
  [string]$CodexHome = $(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }),
  [string]$ConfigPath = '',
  [string]$DevSpaceConfigPath = '',
  [ValidateSet('Once', 'Watch')]
  [string]$Mode = 'Once',
  [ValidateRange(0, 86400)]
  [int]$WatchIntervalSeconds = 30,
  [ValidateRange(0, 86400)]
  [int]$FailureRetrySeconds = 60,
  [ValidateRange(0, 1000000)]
  [int]$MaxCycles = 0,
  [ValidatePattern('^[A-Za-z0-9_.-]{1,120}$')]
  [string]$MutexName = 'CodexProDevSpaceBootstrap'
)

$ErrorActionPreference = 'Stop'
$CodexRoot = [IO.Path]::GetFullPath($CodexHome)
if (!$ConfigPath) { $ConfigPath = Join-Path $CodexRoot 'config/codexpro-devspace-bootstrap.json' }
$ConfigPath = [IO.Path]::GetFullPath($ConfigPath)
if (!$DevSpaceConfigPath) { $DevSpaceConfigPath = Join-Path $env:USERPROFILE '.devspace/config.json' }
$DevSpaceConfigPath = [IO.Path]::GetFullPath($DevSpaceConfigPath)
$LogRoot = Join-Path $CodexRoot 'logs/codexpro-devspace'
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$LogPath = Join-Path $LogRoot ("bootstrap-{0}.log" -f (Get-Date -Format 'yyyy-MM'))

function Write-BootstrapLog([string]$Message) {
  Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
}

function Get-TextSha256([string]$Text) {
  $Hasher = [Security.Cryptography.SHA256]::Create()
  try {
    $Bytes = [Text.Encoding]::UTF8.GetBytes($Text)
    return ([BitConverter]::ToString($Hasher.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant()
  } finally {
    $Hasher.Dispose()
  }
}

$Mutex = New-Object Threading.Mutex($false, ("Local\{0}" -f $MutexName))
$Acquired = $false
try {
  $Acquired = $Mutex.WaitOne(0)
  if (!$Acquired) { exit 0 }
  Write-BootstrapLog ("Bootstrap started in {0} mode." -f $Mode)
  if (!(Test-Path -LiteralPath $ConfigPath)) { throw "Bootstrap config missing: $ConfigPath" }
  $Config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
  if ([string]$Config.schema -ne 'codexpro.devspace-bootstrap/v1') { throw 'Unsupported bootstrap config schema.' }
  if (!(Test-Path -LiteralPath $DevSpaceConfigPath)) { throw "DevSpace config missing: $DevSpaceConfigPath" }
  $Python = [string]$Config.python_path
  if (!$Python) {
    $PythonCommand = Get-Command python.exe,python -ErrorAction SilentlyContinue | Select-Object -First 1
    if (!$PythonCommand) { throw 'Python is unavailable.' }
    $Python = $PythonCommand.Source
  }
  if (!(Test-Path -LiteralPath $Python)) { throw "Python is unavailable: $Python" }
  $Helper = Join-Path $CodexRoot 'skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py'
  if (!(Test-Path -LiteralPath $Helper)) { throw "DevSpace recovery helper missing: $Helper" }
  if (Test-Path -LiteralPath 'C:\Program Files\Tailscale') {
    $env:PATH = 'C:\Program Files\Tailscale;' + $env:PATH
  }
  function Get-RecoveryInvocation {
    $RawConfig = Get-Content -LiteralPath $DevSpaceConfigPath -Raw -Encoding UTF8
    $LiveConfig = $RawConfig | ConvertFrom-Json
    $LiveRoots = @($LiveConfig.allowedRoots)
    if ($LiveRoots.Count -eq 0) { throw 'DevSpace config allowedRoots is missing or empty.' }
    $Arguments = @($Helper, 'recover')
    foreach ($Root in $LiveRoots) {
      if (![IO.Path]::IsPathRooted([string]$Root)) { throw "DevSpace config allowedRoot is not absolute: $Root" }
      $Arguments += @('--root', [IO.Path]::GetFullPath([string]$Root))
    }
    $Arguments += @('--hostname', [string]$Config.hostname)
    if ($Config.local_port) { $Arguments += @('--local-port', [string]$Config.local_port) }
    if ($Config.public_port) { $Arguments += @('--public-port', [string]$Config.public_port) }
    return [pscustomobject]@{ Arguments = $Arguments; RootCount = $LiveRoots.Count; ConfigSha256 = Get-TextSha256 $RawConfig }
  }

  $Cycle = 0
  $PreviouslyHealthy = $false
  $PreviousConfigSha256 = ''
  while ($true) {
    $Cycle++
    $Healthy = $false
    for ($Attempt = 1; $Attempt -le 6; $Attempt++) {
      $PreviousPreference = $ErrorActionPreference
      $RecoveryOutput = @()
      try {
        $Invocation = Get-RecoveryInvocation
        if ($Invocation.ConfigSha256 -ne $PreviousConfigSha256) {
          Write-BootstrapLog ("Loaded {0} allowed roots from {1} (sha256={2})." -f $Invocation.RootCount, $DevSpaceConfigPath, $Invocation.ConfigSha256)
          $PreviousConfigSha256 = $Invocation.ConfigSha256
        }
        $ErrorActionPreference = 'Continue'
        $Arguments = @($Invocation.Arguments)
        $RecoveryOutput = @(& $Python @Arguments 2>&1)
        $ExitCode = $LASTEXITCODE
      } finally {
        $ErrorActionPreference = $PreviousPreference
      }
      if ($ExitCode -eq 0) {
        $RecoveryText = ($RecoveryOutput -join [Environment]::NewLine).Trim()
        try { $Recovery = $RecoveryText | ConvertFrom-Json -ErrorAction Stop } catch {
          Write-BootstrapLog "Recovery cycle $Cycle attempt $Attempt returned invalid JSON (sha256=$(Get-TextSha256 $RecoveryText))."
          $ExitCode = 1
          continue
        }
        if ([bool]$Recovery.service_started) {
          $Service = $Recovery.service
          Write-BootstrapLog ("DevSpace restarted (cycle={0}, attempt={1}, supervisor_pid={2}, child_pid={3}, state={4})." -f $Cycle, $Attempt, $Service.supervisor_pid, $Service.child_pid, $Service.state_path)
        }
        $Healthy = $true
        break
      }
      Write-BootstrapLog "Recovery cycle $Cycle attempt $Attempt failed with exit code $ExitCode (output_sha256=$(Get-TextSha256 (($RecoveryOutput -join [Environment]::NewLine).Trim()))."
      if ($Attempt -lt 6) { Start-Sleep -Seconds 15 }
    }

    if ($Healthy) {
      if (!$PreviouslyHealthy) {
        Write-BootstrapLog "DevSpace and Funnel are healthy (cycle $Cycle, attempt $Attempt)."
      }
      $PreviouslyHealthy = $true
      if ($Mode -eq 'Once' -or ($MaxCycles -gt 0 -and $Cycle -ge $MaxCycles)) { exit 0 }
      Start-Sleep -Seconds $WatchIntervalSeconds
      continue
    }

    $PreviouslyHealthy = $false
    if ($Mode -eq 'Once') { throw 'DevSpace recovery retries exhausted.' }
    Write-BootstrapLog ("Recovery cycle {0} exhausted; watchdog remains active." -f $Cycle)
    if ($MaxCycles -gt 0 -and $Cycle -ge $MaxCycles) { exit 1 }
    Start-Sleep -Seconds $FailureRetrySeconds
  }
} catch {
  Write-BootstrapLog ("Bootstrap failed: {0}" -f $_.Exception.Message)
  exit 1
} finally {
  if ($Acquired) { $Mutex.ReleaseMutex() }
  $Mutex.Dispose()
}
