#Requires -Version 7.0
<#
.SYNOPSIS
Export a NAS Storage Analyzer report to a local JSON.GZ file over existing SSH access.
.EXAMPLE
.\tools\storage_analyzer\Export-StorageAnalyzer.ps1 -Nas user@nas
.EXAMPLE
.\tools\storage_analyzer\Export-StorageAnalyzer.ps1 -Nas user@nas -Profile 'Analyze storage space' -OutputPath D:\reports\nas.json.gz
#>
[CmdletBinding(DefaultParameterSetName = 'Profile')]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^(?!-)\S+$')]
    [string]$Nas,
    [Parameter(ParameterSetName = 'Profile')]
    [string]$Profile,
    [Parameter(Mandatory, ParameterSetName = 'Report')]
    [string]$ReportPath,
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
function Quote-RemoteArgument([string]$Value) {
    "'" + $Value.Replace("'", "'\''") + "'"
}

$remoteCommand = 'nice -n 19 python3 - --base64'
if ($ReportPath) { $remoteCommand += ' --report ' + (Quote-RemoteArgument $ReportPath) }
if ($Profile) { $remoteCommand += ' --profile ' + (Quote-RemoteArgument $Profile) }
$code = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'export_storage_analyzer.py'))
$previousEncoding = $OutputEncoding
try {
    $OutputEncoding = [Text.UTF8Encoding]::new($false)
    $response = $code | & ssh -T -o BatchMode=yes -o ConnectTimeout=10 `
        -o StrictHostKeyChecking=yes -o UpdateHostKeys=no $Nas $remoteCommand
    if ($LASTEXITCODE -ne 0) { throw "NAS export failed (SSH exit $LASTEXITCODE); no output file was written." }
} finally {
    $OutputEncoding = $previousEncoding
}

$envelope = ($response -join "`n") | ConvertFrom-Json
$payload = [Convert]::FromBase64String($envelope.data)
$hasher = [Security.Cryptography.SHA256]::Create()
try {
    $checksum = [BitConverter]::ToString($hasher.ComputeHash($payload)).Replace('-', '').ToLowerInvariant()
} finally {
    $hasher.Dispose()
}
if ($checksum -ne $envelope.sha256) { throw 'Export checksum mismatch; no output file was written.' }
if (-not $OutputPath) {
    $filename = '{0}-{1}-logical.json.gz' -f $envelope.source.host, $envelope.source.report
    $downloads = Get-ItemPropertyValue `
        -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' `
        -Name '{374DE290-123F-4565-9164-39C4925E467B}'
    $OutputPath = Join-Path ([Environment]::ExpandEnvironmentVariables($downloads)) $filename
}
$OutputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputPath)
# CreateNew refuses to replace an existing export, including when two exports run at once.
$destination = [IO.File]::Open($OutputPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
try { $destination.Write($payload, 0, $payload.Length) } finally { $destination.Dispose() }
Write-Host ('Report {0}: {1:N0} logical bytes, {2:N0} files, {3:N0} directories' -f `
    $envelope.source.report, $envelope.source.logical_bytes, $envelope.source.files, $envelope.source.directories)
Get-Item -LiteralPath $OutputPath
