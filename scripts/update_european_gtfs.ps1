<#
Stages official European rail timetable files for the future shared OTP graph.

France can be downloaded directly from the national transport-data portal.
Austria, Switzerland and Italy deliberately require a local source file: their
official portals either require acceptance of terms, publish a dynamic download
resource, or use NeTEx rather than a ready GTFS archive. This avoids silently
using an unofficial mirror or accepting licence terms on the user's behalf.
#>

param(
    [ValidateSet("France", "Austria", "Switzerland", "Italy")]
    [string]$Country,
    [string]$SourcePath
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$rawDirectory = Join-Path $projectRoot "otp-data\europe\raw"
$destinations = @{
    France = "france-sncf-gtfs.zip"
    Austria = "austria-oebb-gtfs.zip"
    Switzerland = "switzerland-national-gtfs.zip"
    Italy = "italy-national-gtfs.zip"
}
$destination = Join-Path $rawDirectory $destinations[$Country]

New-Item -ItemType Directory -Force -Path $rawDirectory | Out-Null

if ($Country -eq "France" -and -not $SourcePath) {
    $feedUrl = "https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip"
    Invoke-WebRequest -Uri $feedUrl -OutFile $destination
    Write-Host "Official SNCF GTFS feed saved to $destination"
    exit 0
}

if ($Country -eq "Switzerland" -and -not $SourcePath) {
    # The catalogue issues a fresh signed download URL for every published
    # timetable. Read the first current GTFS resource instead of hard-coding
    # an expiring storage URL.
    $catalogueUrl = "https://data.opentransportdata.swiss/en/dataset/timetable-2026-gtfs2020"
    $catalogue = Invoke-WebRequest -Uri $catalogueUrl -Headers @{ "User-Agent" = "Mozilla/5.0" }
    $feedUrl = ($catalogue.Links |
        Where-Object { $_.href -match "/download/gtfs_fp2026_.*\.zip$" } |
        Select-Object -First 1 -ExpandProperty href)
    if (-not $feedUrl) {
        throw "The Swiss catalogue did not expose a current GTFS download URL. Download it manually and use -SourcePath."
    }
    Invoke-WebRequest -Uri $feedUrl -Headers @{ "User-Agent" = "Mozilla/5.0" } -OutFile $destination
    Write-Host "Official Swiss national GTFS feed saved to $destination"
    exit 0
}

if (-not $SourcePath) {
    throw "Download the official $Country timetable first, then pass its archive with -SourcePath. No unofficial download is used automatically."
}
if (-not (Test-Path -LiteralPath $SourcePath)) {
    throw "SourcePath was not found: $SourcePath"
}
Copy-Item -LiteralPath $SourcePath -Destination $destination -Force
Write-Host "$Country timetable staged at $destination"
