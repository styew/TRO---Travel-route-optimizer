<#
Downloads the official free German long-distance GTFS feed used by the
separate OpenTripPlanner graph. This includes ICE, IC, EC, railJet and night
trains, but does not replace the Deutschlandticket-only regional graph.
#>

$projectRoot = Split-Path -Parent $PSScriptRoot
$dataDirectory = Join-Path $projectRoot "otp-data\long-distance"
$destination = Join-Path $dataDirectory "germany-long-distance-gtfs.zip"
$feedUrl = "https://download.gtfs.de/germany/fv_free/latest.zip"

New-Item -ItemType Directory -Force -Path $dataDirectory | Out-Null
Invoke-WebRequest -Uri $feedUrl -OutFile $destination
Write-Host "German long-distance GTFS feed saved to $destination"
