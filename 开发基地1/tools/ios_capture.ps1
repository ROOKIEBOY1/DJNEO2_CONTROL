param(
  [switch]$List,
  [string]$ProcessName = 'DJI Fly',
  [string]$Interface = '',
  [string]$Output = ''
)

$ErrorActionPreference = 'Stop'
$pmd = 'D:\pocket\control\.venv-ios\Scripts\pymobiledevice3.exe'

if (-not (Test-Path -LiteralPath $pmd)) {
  throw "pymobiledevice3 not found at $pmd"
}

$devices = & $pmd usbmux list
if ($devices -eq '[]') {
  throw 'No iPhone detected. Unlock it, connect USB, and trust this computer.'
}

if ($List) {
  & $pmd usbmux list
  & $pmd apps list | Select-String -Pattern 'dji|fly' -CaseSensitive:$false
  & $pmd processes ps | Select-String -Pattern 'dji|fly' -CaseSensitive:$false
  exit 0
}

if ([string]::IsNullOrWhiteSpace($Output)) {
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $Output = "D:\pocket\control\captures\iphone-$stamp.pcapng"
}

$outputDir = Split-Path -Parent $Output
if (-not (Test-Path -LiteralPath $outputDir)) {
  New-Item -ItemType Directory -Path $outputDir | Out-Null
}

$captureArgs = @('pcap', '--out', $Output)
if (-not [string]::IsNullOrWhiteSpace($ProcessName)) {
  $captureArgs += @('--process', $ProcessName)
}
if (-not [string]::IsNullOrWhiteSpace($Interface)) {
  $captureArgs += @('--interface', $Interface)
}

Write-Host "Capturing iPhone traffic to $Output"
Write-Host 'Press Ctrl+C to stop.'
& $pmd @captureArgs | Out-Null
