# Builds house-recon-companion.zip for store upload (manifest at zip root).
# The store build strips the localhost/127.0.0.1 entries — those exist only
# for local development of the analyzer page; reviewers should not see
# permissions the shipped extension doesn't need.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$out = Join-Path $here "house-recon-companion.zip"
$stage = Join-Path $env:TEMP "hr-ext-stage"
if (Test-Path $out) { Remove-Item $out -Force }
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage | Out-Null

$manifest = Get-Content (Join-Path $here "manifest.json") -Raw -Encoding UTF8 |
    ConvertFrom-Json
$manifest.host_permissions = @($manifest.host_permissions |
    Where-Object { $_ -notmatch "localhost|127\.0\.0\.1" })
foreach ($cs in $manifest.content_scripts) {
    $cs.matches = @($cs.matches | Where-Object { $_ -notmatch "localhost|127\.0\.0\.1" })
}
$manifest | ConvertTo-Json -Depth 8 | Out-File (Join-Path $stage "manifest.json") -Encoding utf8

Copy-Item (Join-Path $here "background.js"), (Join-Path $here "content.js") $stage
Copy-Item (Join-Path $here "icons") (Join-Path $stage "icons") -Recurse
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $out
Remove-Item $stage -Recurse -Force
Write-Host "Built $out (store manifest: localhost entries stripped)"
