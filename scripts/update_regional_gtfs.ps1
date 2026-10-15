<#
Downloads the current free German regional-rail GTFS feed used by OpenTripPlanner.
The generated file is local runtime data and is deliberately excluded from Git.
#>

$projectRoot = Split-Path -Parent $PSScriptRoot
$dataDirectory = Join-Path $projectRoot "otp-data"
$destination = Join-Path $dataDirectory "regional-rail-gtfs.zip"
$feedUrl = "https://download.gtfs.de/germany/rv_free/latest.zip"

New-Item -ItemType Directory -Force -Path $dataDirectory | Out-Null
Invoke-WebRequest -Uri $feedUrl -OutFile $destination
Write-Host "Regional GTFS feed saved to $destination"
