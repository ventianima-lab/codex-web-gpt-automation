[CmdletBinding(SupportsShouldProcess=$true)]
param([Parameter(Mandatory=$true)][string]$Receipt,[string]$CodexHome=$(if($env:CODEX_HOME){$env:CODEX_HOME}else{Join-Path $env:USERPROFILE '.codex'}))
$ErrorActionPreference='Stop';$CodexRoot=[IO.Path]::GetFullPath($CodexHome);$ReceiptRoot=Join-Path $CodexRoot 'receipts'
function Get-Hash([string]$p){
  $stream=$null;$sha256=$null
  try{
    $stream=[IO.File]::Open($p,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    $sha256=[Security.Cryptography.SHA256]::Create()
    (([BitConverter]::ToString($sha256.ComputeHash($stream))) -replace '-','').ToLowerInvariant()
  } finally {
    if($sha256){$sha256.Dispose()}
    if($stream){$stream.Dispose()}
  }
}
function Test-IsWithinRoot([string]$r,[string]$p){$r=[IO.Path]::GetFullPath($r).TrimEnd([IO.Path]::DirectorySeparatorChar,[IO.Path]::AltDirectorySeparatorChar);$p=[IO.Path]::GetFullPath($p);$p.StartsWith($r+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)}
function Get-SafeChild([string]$r,[string]$x){if([string]::IsNullOrWhiteSpace($x)-or[IO.Path]::IsPathRooted($x)-or$x -match '(^|[\\/])\.{1,2}([\\/]|$)'){throw "unsafe relative path: $x"};$p=[IO.Path]::GetFullPath((Join-Path $r $x));if(!(Test-IsWithinRoot $r $p)){throw "path escapes root: $x"};$p}
$fullReceipt=[IO.Path]::GetFullPath($Receipt);if(!(Test-IsWithinRoot $ReceiptRoot $fullReceipt) -or !(Test-Path -LiteralPath $fullReceipt) -or (Get-Item -LiteralPath $fullReceipt -Force).LinkType){throw 'receipt must be owned by this CODEX_HOME'}
$value=Get-Content -LiteralPath $fullReceipt -Raw|ConvertFrom-Json;if($value.schema -notin @('codexpro.install-receipt/v2','codexpro.install-receipt/v3')){throw 'unsupported receipt schema'};if(!(Test-IsWithinRoot (Join-Path $CodexRoot 'backups') $value.backup)){throw 'receipt backup must be owned by this CODEX_HOME'}
$localMultiReceipt=[string]$value.optional_components.local_multi_gpt.receipt;$localMultiHelper=Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'bin/codex_local_multi_gpt_setup.py';$localMultiPreflight=$null;if($localMultiReceipt){$localPreflightOutput=@(& python $localMultiHelper rollback --dry-run --codex-home $CodexRoot --receipt $localMultiReceipt);if($LASTEXITCODE){$localMultiPreflight=@{path='config.toml';action='local_multi_gpt_registration_preflight_incomplete';detail=($localPreflightOutput -join ' ')}};if($localMultiPreflight){[ordered]@{schema='codexpro.rollback-result/v1';receipt=$fullReceipt;status='CONFLICT';conflicts=@($localMultiPreflight)}|ConvertTo-Json -Depth 5;exit 2}}
$dependencyPreflight=$null;if($value.schema -eq 'codexpro.install-receipt/v3' -and $value.dependency.mode -eq 'applied'){$dependencyReceipt=[string]$value.dependency.receipt;if(!(Test-IsWithinRoot $value.backup $dependencyReceipt) -or !(Test-Path -LiteralPath $dependencyReceipt) -or (Get-Hash $dependencyReceipt)-ne [string]$value.dependency.receipt_sha256){$dependencyPreflight=@{path='dependency';action='dependency_receipt_invalid'}}else{& (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'update.ps1') -RollbackReceipt $dependencyReceipt -CodexHome $CodexRoot -WhatIf;if($LASTEXITCODE){$dependencyPreflight=@{path='dependency';action='dependency_preflight_incomplete';exit_code=$LASTEXITCODE}}};if($dependencyPreflight){[ordered]@{schema='codexpro.rollback-result/v1';receipt=$fullReceipt;status='CONFLICT';conflicts=@($dependencyPreflight)}|ConvertTo-Json -Depth 5;exit 2}}
$retirementConflicts=@()
foreach($record in $value.files){
  if($record.action -ne 'retired'){continue}
  $relative=[string]$record.path
  $destination=Get-SafeChild $CodexRoot $relative
  $backup=Get-SafeChild $value.backup $relative
  if(!(Test-Path -LiteralPath $backup -PathType Leaf) -or (Get-Item -LiteralPath $backup -Force).LinkType -or (Get-Hash $backup)-ne $record.backup_sha256){
    $retirementConflicts+=@{path=$relative;action='retirement_backup_invalid'}
    continue
  }
  if(Test-Path -LiteralPath $destination){
    if(!(Test-Path -LiteralPath $destination -PathType Leaf) -or (Get-Item -LiteralPath $destination -Force).LinkType -or (Get-Hash $destination)-ne $record.backup_sha256){
      $retirementConflicts+=@{path=$relative;action='preserved_recreated_retired_path'}
    }
  }
}
if($retirementConflicts.Count){
  [ordered]@{schema='codexpro.rollback-result/v1';receipt=$fullReceipt;status='CONFLICT';conflicts=$retirementConflicts}|ConvertTo-Json -Depth 5
  exit 2
}
$conflicts=@();foreach($record in $value.files){$destination=Get-SafeChild $CodexRoot $record.path;if($record.action -eq 'retired'){$relative=[string]$record.path;$backup=Get-SafeChild $value.backup $relative;if(!(Test-Path -LiteralPath $backup) -or (Get-Item -LiteralPath $backup -Force).LinkType -or (Get-Hash $backup)-ne $record.backup_sha256){throw "retirement backup invalid: $relative"};if(Test-Path -LiteralPath $destination){continue};if($PSCmdlet.ShouldProcess($destination,'restore receipt-retired file')){New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination)|Out-Null;Copy-Item -LiteralPath $backup -Destination $destination -Force};continue};if($record.action -eq 'created'){if(!(Test-Path -LiteralPath $destination)){continue};if((Get-Item -LiteralPath $destination -Force).LinkType){throw "destination symlink refused: $($record.path)"};if((Get-Hash $destination)-eq $record.installed_sha256){if($PSCmdlet.ShouldProcess($destination,'remove unchanged receipt-created file')){Remove-Item -LiteralPath $destination -Force}}else{$conflicts+=@{path=$record.path;action='preserved_modified_created'}};continue};if($record.action -ne 'overwritten'){throw "invalid receipt action: $($record.action)"};$relative=[string]$record.path;$backup=Get-SafeChild $value.backup $relative;if(!(Test-Path -LiteralPath $backup) -or (Get-Item -LiteralPath $backup -Force).LinkType -or (Get-Hash $backup)-ne $record.backup_sha256){throw "backup invalid: $relative"};if((Test-Path -LiteralPath $destination) -and !(Get-Item -LiteralPath $destination -Force).LinkType -and (Get-Hash $destination)-eq $record.installed_sha256){if($PSCmdlet.ShouldProcess($destination,'restore unchanged receipt-overwritten file')){Copy-Item -LiteralPath $backup -Destination $destination -Force}}else{$conflicts+=@{path=$relative;action='preserved_modified_overwritten'}}}
$dependencyConflict=$null;if(!$conflicts.Count -and $value.schema -eq 'codexpro.install-receipt/v3' -and $value.dependency.mode -eq 'applied'){$dependencyReceipt=[string]$value.dependency.receipt;if(!(Test-IsWithinRoot $value.backup $dependencyReceipt) -or !(Test-Path -LiteralPath $dependencyReceipt) -or (Get-Hash $dependencyReceipt)-ne [string]$value.dependency.receipt_sha256){$dependencyConflict=@{path='dependency';action='dependency_receipt_invalid'}};if(!$dependencyConflict){& (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'update.ps1') -RollbackReceipt $dependencyReceipt -CodexHome $CodexRoot -WhatIf:$WhatIfPreference;if($LASTEXITCODE){$dependencyConflict=@{path='dependency';action='dependency_rollback_incomplete';exit_code=$LASTEXITCODE}}}}
$localMultiConflict=$null;if(!$conflicts.Count -and !$dependencyConflict -and $localMultiReceipt){$localRollbackOutput=@(& python $localMultiHelper rollback --codex-home $CodexRoot --receipt $localMultiReceipt $(if($WhatIfPreference){'--dry-run'}));if($LASTEXITCODE){$localMultiConflict=@{path='config.toml';action='local_multi_gpt_registration_rollback_incomplete';detail=($localRollbackOutput -join ' ')}}}
$status=if($conflicts.Count){'CONFLICT'}elseif($dependencyConflict -or $localMultiConflict){'PARTIAL'}else{'COMPLETE'};$result=[ordered]@{schema='codexpro.rollback-result/v1';receipt=$fullReceipt;status=$status;conflicts=$conflicts;dependency=$dependencyConflict;local_multi_gpt=$localMultiConflict};$result|ConvertTo-Json -Depth 5
if($conflicts.Count){exit 2};if($dependencyConflict -or $localMultiConflict){exit 3}
