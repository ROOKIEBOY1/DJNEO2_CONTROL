$ErrorActionPreference = 'Stop'

$project = Split-Path -Parent $PSScriptRoot
$workspace = Split-Path -Parent $project
$python = Join-Path $workspace '.抓包环境\Scripts\python.exe'
$probe = Join-Path $PSScriptRoot 'handshake_probe.py'
$captures = Join-Path $project 'captures'

if (-not (Test-Path -LiteralPath $python)) {
  throw "Python environment not found: $python"
}

$addresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
  Where-Object { $_.IPAddress -like '192.168.2.*' -and $_.IPAddress -ne '192.168.2.1' })
if ($addresses.Count -ne 1) {
  throw "Expected one computer address on drone Wi-Fi (192.168.2.x); found $($addresses.Count). Check the Wi-Fi connection."
}

New-Item -ItemType Directory -Force -Path $captures | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$output = Join-Path $captures "handshake-probe-$stamp.json"
$result = & $python $probe --send --local-ip $addresses[0].IPAddress
if ($LASTEXITCODE -ne 0) {
  throw 'Handshake probe failed. No result was saved.'
}
$result | Set-Content -LiteralPath $output -Encoding utf8
Write-Output "Saved result: $output"
Write-Output $result
