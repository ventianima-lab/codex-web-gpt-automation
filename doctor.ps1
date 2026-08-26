[CmdletBinding()]
param(
  [string]$CodexHome = $(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }),
  [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$CodexRoot = [IO.Path]::GetFullPath($CodexHome)
$ReceiptRoot = Join-Path $CodexRoot 'receipts'
$Issues = @()
$Warnings = @()
$Commands = @('powershell -ExecutionPolicy Bypass -File .\install.ps1 -WhatIf')
$LocalMultiGptEnabled = $false
$LocalMultiGptDoctor = $null
$LegacyDependencyMode = $null
$InstallReceiptSchema = $null

function Get-Sha256([string]$Path) {
  $stream = $null
  $sha256 = $null
  try {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $sha256 = [Security.Cryptography.SHA256]::Create()
    (([BitConverter]::ToString($sha256.ComputeHash($stream))) -replace '-', '').ToLowerInvariant()
  } finally {
    if ($sha256) { $sha256.Dispose() }
    if ($stream) { $stream.Dispose() }
  }
}

function Test-IsWithinRoot([string]$Root, [string]$Path) {
  $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd([IO.Path]::DirectorySeparatorChar,[IO.Path]::AltDirectorySeparatorChar)
  $candidate = [IO.Path]::GetFullPath($Path)
  $candidate.StartsWith($rootPath + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)
}

function Get-SafeChild([string]$Root, [string]$Relative) {
  if ([string]::IsNullOrWhiteSpace($Relative) -or [IO.Path]::IsPathRooted($Relative) -or $Relative -match '(^|[\/])\.{1,2}([\/]|$)') {
    throw "unsafe receipt path: $Relative"
  }
  $candidate = [IO.Path]::GetFullPath((Join-Path $Root $Relative))
  if (!(Test-IsWithinRoot $Root $candidate)) { throw "receipt path escapes CODEX_HOME: $Relative" }
  $candidate
}

$Receipt = Get-ChildItem -LiteralPath $ReceiptRoot -Filter 'codexpro-automation-*.json' -File -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
if (!$Receipt) {
  $Issues += @{code='RECEIPT_MISSING'; detail='No install receipt found'}
} else {
  try {
    $Value = Get-Content -LiteralPath $Receipt.FullName -Raw | ConvertFrom-Json
    if (@('codexpro.install-receipt/v2','codexpro.install-receipt/v3') -notcontains [string]$Value.schema) {
      throw 'unsupported install receipt schema'
    }
    $InstallReceiptSchema = [string]$Value.schema
    foreach ($Record in $Value.files) {
      $Path = Get-SafeChild $CodexRoot ([string]$Record.path)
      if (!(Test-Path -LiteralPath $Path)) {
        $Issues += @{code='FILE_MISSING'; path=$Record.path}
        continue
      }
      $Actual = Get-Sha256 $Path
      if ($Actual -ne $Record.installed_sha256) {
        $Issues += @{code='HASH_MISMATCH'; path=$Record.path; actual=$Actual}
      }
    }
    $LocalMultiGptEnabled = [bool]$Value.optional_components.local_multi_gpt.enabled
    $LegacyDependencyMode = [string]$Value.dependency.mode
  } catch {
    $Issues += @{code='RECEIPT_INVALID'; detail=$_.Exception.Message}
  }
}

$Agbrowse = Get-Command agbrowse.cmd,agbrowse -ErrorAction SilentlyContinue | Select-Object -First 1
if (!$Agbrowse) {
  $Warnings += @{code='LEGACY_AGBROWSE_MISSING'; detail='Only legacy run recovery is unavailable'}
  $Commands += 'powershell -ExecutionPolicy Bypass -File .\update.ps1 -AgbrowseVersion 0.1.18'
}

$Node = Get-Command node.exe,node -ErrorAction SilentlyContinue | Select-Object -First 1
$Npx = Get-Command npx.cmd,npx -ErrorAction SilentlyContinue | Select-Object -First 1
$GitBash = Get-Item -LiteralPath 'C:\Program Files\Git\bin\bash.exe' -ErrorAction SilentlyContinue
if (!$Node -or !$Npx) {
  $Issues += @{code='ORACLE_DEVSPACE_NODE_TOOLING_MISSING'; detail='Node and npx are required for Oracle and DevSpace'}
} else {
  try {
    $NodeVersion = (& $Node.Source --version).Trim().TrimStart('v')
    $NodeMajor = [int]($NodeVersion.Split('.')[0])
    if ($NodeMajor -lt 24 -or $NodeMajor -ge 27) {
      $Issues += @{code='ORACLE_DEVSPACE_NODE_VERSION_UNSUPPORTED'; actual=$NodeVersion; required='>=24 <27'}
    }
  } catch {
    $Issues += @{code='NODE_VERSION_UNREADABLE'; detail=$_.Exception.Message}
  }
}
if (!$GitBash) {
  $Issues += @{code='DEVSPACE_GIT_BASH_MISSING'; detail='Windows DevSpace requires Git Bash'}
}
$Commands += 'npx -y @steipete/oracle@0.18.0 --version'
$Commands += 'python .\skills\chatgpt-workspace-setup\scripts\devspace_tailscale_setup.py doctor --root C:\project --hostname your-device.your-tailnet.ts.net'

$Python = Get-Command python.exe,python -ErrorAction SilentlyContinue | Select-Object -First 1
if ($LocalMultiGptEnabled) {
  if (!$Python) {
    $Issues += @{code='LOCAL_MULTI_GPT_PYTHON_MISSING'}
  } else {
    $LocalMultiOutput = @(& $Python.Source (Join-Path $CodexRoot 'bin/codex_local_multi_gpt_setup.py') doctor --codex-home $CodexRoot)
    try { $LocalMultiGptDoctor = ($LocalMultiOutput -join [Environment]::NewLine) | ConvertFrom-Json } catch { $LocalMultiGptDoctor = $null }
    if ($LASTEXITCODE -or !$LocalMultiGptDoctor -or !$LocalMultiGptDoctor.ok) {
      $Issues += @{code='LOCAL_MULTI_GPT_MCP_INVALID'; detail=($LocalMultiOutput -join ' ')}
    }
  }
}
$UpdateReceiptPath = Join-Path $CodexRoot 'agbrowse-update-receipt.json'
$UpdateReceipt = $null
$SelectedVersion = '0.1.18'
$SelectedIntegrity = $null
$Contract = Join-Path $CodexRoot 'contracts/agbrowse-0.1.18.json'
if (Test-Path -LiteralPath $UpdateReceiptPath) {
  try {
    $UpdateReceipt = Get-Content -LiteralPath $UpdateReceiptPath -Raw | ConvertFrom-Json
    if ($UpdateReceipt.schema -ne 'codexpro.agbrowse-update-receipt/v2') { throw 'unsupported update receipt schema' }
    $SelectedVersion = [string]$UpdateReceipt.selected_version
    $SelectedIntegrity = [string]$UpdateReceipt.integrity
    $Contract = [IO.Path]::GetFullPath([string]$UpdateReceipt.contract)
    if (!(Test-IsWithinRoot (Join-Path $CodexRoot 'contracts') $Contract)) { throw 'update contract path escapes CODEX_HOME' }
  } catch {
    $Issues += @{code='UPDATE_RECEIPT_INVALID'; detail=$_.Exception.Message}
    $UpdateReceipt = $null
  }
}

$VerifyLegacyContract = (
  [bool]$UpdateReceipt -or
  $LegacyDependencyMode -eq 'applied' -or
  $InstallReceiptSchema -eq 'codexpro.install-receipt/v2'
)
if ($Agbrowse -and !$VerifyLegacyContract) {
  $Warnings += @{code='LEGACY_AGBROWSE_UNMANAGED'; detail='PATH agbrowse is outside this install receipt and was not contract-validated'}
}
if ($VerifyLegacyContract -and (!$Python -or !(Test-Path -LiteralPath $Contract))) {
  $Issues += @{code='CONTRACT_UNVERIFIED'; detail='Python or contract manifest unavailable'}
} elseif ($VerifyLegacyContract) {
  if ($UpdateReceipt -and (Get-Sha256 $Contract) -ne [string]$UpdateReceipt.contract_sha256) {
    $Issues += @{code='CONTRACT_RECEIPT_HASH_MISMATCH'; contract=$Contract}
  } else {
    $Arguments = @(
      (Join-Path $CodexRoot 'bin/chatgpt_agbrowse_contract.py'),
      'validate', '--manifest', $Contract
    )
    if ($SelectedIntegrity) {
      $Arguments += @('--expected-version',$SelectedVersion,'--expected-integrity',$SelectedIntegrity)
    }
    & $Python.Source @Arguments
    if ($LASTEXITCODE) { $Issues += @{code='CONTRACT_INVALID'; contract=$Contract} }
  }

  if ($Agbrowse) {
    try {
      $ContractValue = Get-Content -LiteralPath $Contract -Raw | ConvertFrom-Json
      $ActualExecutableHash = Get-Sha256 $Agbrowse.Source
      if ($ActualExecutableHash -ne $ContractValue.agbrowse.executableSha256) {
        $Issues += @{code='AGBROWSE_EXECUTABLE_HASH_MISMATCH'; actual=$ActualExecutableHash; contract=$ContractValue.agbrowse.executableSha256}
      }
      if ($UpdateReceipt -and $ActualExecutableHash -ne [string]$UpdateReceipt.executable_sha256) {
        $Issues += @{code='AGBROWSE_UPDATE_RECEIPT_EXECUTABLE_MISMATCH'; actual=$ActualExecutableHash}
      }
    } catch {
      $Issues += @{code='CONTRACT_READ_FAILED'; detail=$_.Exception.Message}
    }
  }
}

[ordered]@{
  schema = 'codexpro.doctor/v2'
  codex_home = $CodexRoot
  receipt = $(if ($Receipt) { $Receipt.FullName } else { $null })
  status = $(if ($Issues) { 'FAIL' } else { 'PASS' })
  issues = $Issues
  warnings = $Warnings
  commands = $Commands
  agbrowse = @{selected_version=$SelectedVersion; contract=$Contract; update_receipt=$UpdateReceiptPath}
  oracle = @{package='@steipete/oracle';current_version='0.18.0';last_known_good='0.17.1';policy='newest-validated-stable';resolution='npx at explicit run time'}
  devspace = @{package='@waishnav/devspace';current_version='1.0.8';last_known_good='1.0.7';policy='newest-validated-stable';setup='explicit setup skill only'}
  local_multi_gpt = @{enabled=$LocalMultiGptEnabled;doctor=$LocalMultiGptDoctor}
  codexpro = @{
    installation = 'external'
    detail = 'CodexPro is not installed by install.ps1; app bootstrap scripts acquire the latest supported external runtime.'
  }
  what_if = [bool]$WhatIf
} | ConvertTo-Json -Depth 7
if ($Issues) { exit 1 }
