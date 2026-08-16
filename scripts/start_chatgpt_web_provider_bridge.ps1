[CmdletBinding()]
param(
  [string]$CodexHome = $(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }),
  [ValidateSet('Once', 'Watch')]
  [string]$Mode = 'Once',
  [ValidateRange(5, 3600)]
  [int]$RestartDelaySeconds = 15,
  [ValidateRange(0, 1000000)]
  [int]$MaxCycles = 0
)

$ErrorActionPreference = 'Stop'
$CodexRoot = [IO.Path]::GetFullPath($CodexHome)
$ConfigPath = Join-Path $CodexRoot 'config/web-chatgpt-provider.json'
$BridgePath = Join-Path $CodexRoot 'bin/chatgpt_web_provider_bridge.py'
$LogRoot = Join-Path $CodexRoot 'logs/web-chatgpt-provider'
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$LauncherLog = Join-Path $LogRoot ("launcher-{0}.log" -f (Get-Date -Format 'yyyy-MM'))

function Write-BridgeLog([string]$Message) {
  Add-Content -LiteralPath $LauncherLog -Encoding UTF8 -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
}

$Mutex = New-Object Threading.Mutex($false, 'Local\CodexWebChatGPTBridge')
$Acquired = $false
try {
  $Acquired = $Mutex.WaitOne(0)
  if (!$Acquired) { exit 0 }
  if (!(Test-Path -LiteralPath $ConfigPath)) { throw "Bridge config missing: $ConfigPath" }
  if (!(Test-Path -LiteralPath $BridgePath)) { throw "Bridge executable missing: $BridgePath" }
  $Config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
  if ([string]$Config.schema -ne 'codex.web-chatgpt-provider/v1') { throw 'Unsupported bridge config schema.' }
  $Python = [string]$Config.python_executable
  if (!(Test-Path -LiteralPath $Python)) { throw "Python is unavailable: $Python" }
  $Cycle = 0
  while ($true) {
    $Cycle++
    Write-BridgeLog "Starting bridge cycle $Cycle."
    $Process = Start-Process -FilePath $Python -ArgumentList @($BridgePath, '--config', $ConfigPath) -WindowStyle Hidden -Wait -PassThru
    $ExitCode = $Process.ExitCode
    Write-BridgeLog "Bridge cycle $Cycle exited with code $ExitCode."
    if ($Mode -eq 'Once' -or ($MaxCycles -gt 0 -and $Cycle -ge $MaxCycles)) { exit $ExitCode }
    Start-Sleep -Seconds $RestartDelaySeconds
  }
} catch {
  Write-BridgeLog ("Launcher failed: {0}" -f $_.Exception.Message)
  exit 1
} finally {
  if ($Acquired) { $Mutex.ReleaseMutex() }
  $Mutex.Dispose()
}
