# Builds house-recon-companion.zip for store upload (manifest at zip root).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$out = Join-Path $here "house-recon-companion.zip"
if (Test-Path $out) { Remove-Item $out -Force }
$items = @("manifest.json", "background.js", "content.js", "icons") |
  ForEach-Object { Join-Path $here $_ }
Compress-Archive -Path $items -DestinationPath $out
Write-Host "Built $out"
