<#
Creates the small, independent France OTP input directory from the already
filtered SNCF archive. It keeps the original Europe archive untouched.

Run once after the SNCF feed has been updated, before the France OTP build:
    .\scripts\rail\prepare_french_rail_graph.ps1
#>

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$source = Join-Path $projectRoot "otp-data\europe\rail\france-sncf-gtfs.zip"
$destinationDirectory = Join-Path $projectRoot "otp-data\france"
$destination = Join-Path $destinationDirectory "france-sncf-gtfs.zip"

if (-not (Test-Path -LiteralPath $source)) {
    throw "France rail archive was not found: $source"
}

New-Item -ItemType Directory -Force -Path $destinationDirectory | Out-Null
Copy-Item -LiteralPath $source -Destination $destination -Force
@'
{
  "transitModelTimeZone": "Europe/Paris"
}
'@ | Set-Content -LiteralPath (Join-Path $destinationDirectory "build-config.json") -Encoding utf8

Write-Host "France OTP input prepared at $destinationDirectory"
