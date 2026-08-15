param(
    [string]$Version = "dev"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$outputDirectory = Join-Path $projectRoot "artifacts\release"
$archiveName = "RAG-Notes-Agent-$Version.zip"
$archivePath = Join-Path $outputDirectory $archiveName

Set-Location $projectRoot
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required to create a clean release archive."
}
if (Test-Path -LiteralPath $archivePath) {
    throw "Release archive already exists: $archivePath"
}

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
# git archive only includes tracked source files. It never packages local databases, model keys, uploads or artifacts.
git archive --format=zip --prefix="RAG-Notes-Agent/" --output=$archivePath HEAD
if ($LASTEXITCODE -ne 0) {
    throw "git archive failed."
}

Write-Output "Created release archive: $archivePath"
