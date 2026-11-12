# Flash the prebuilt ESP32-S3 firmware in one command.
#
#   .\flash.ps1 -Port COM4
#
# Writes bootloader, partition table, application and the animation pack in a
# single esptool transaction. Requires Python + esptool:
#   python -m pip install esptool
param(
    [string]$Port = 'COM4',
    [string]$Baud = '921600'
)
$ErrorActionPreference = 'Stop'

$here = $PSScriptRoot
$targets = [ordered]@{
    '0x0'      = Join-Path $here 'bootloader.bin'
    '0x8000'   = Join-Path $here 'partitions.bin'
    '0x10000'  = Join-Path $here 'firmware.bin'
    '0x210000' = Join-Path (Join-Path $here '..\generated') 'pet-assets.bin'
}

foreach ($entry in $targets.GetEnumerator()) {
    if (!(Test-Path -LiteralPath $entry.Value)) {
        throw "Missing file for offset $($entry.Key): $($entry.Value)"
    }
}

$esptool = Get-Command esptool -ErrorAction SilentlyContinue
if ($esptool) {
    $exe = $esptool.Source
    $prefix = @()
}
else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (!$python) {
        throw 'esptool not found. Install Python, then run: python -m pip install esptool'
    }
    $exe = $python.Source
    $prefix = @('-m', 'esptool')
}

$arguments = @()
$arguments += $prefix
$arguments += @('--chip', 'esp32s3', '--port', $Port, '--baud', $Baud, 'write_flash')
foreach ($entry in $targets.GetEnumerator()) {
    $arguments += $entry.Key
    $arguments += $entry.Value
}

Write-Host "Flashing prebuilt v7.1 to $Port (16MB flash layout)..." -ForegroundColor Cyan
& $exe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "esptool failed with exit code $LASTEXITCODE. If connecting fails, retry with -Baud 115200."
}

Write-Host 'Done. The device reboots and starts playing the whale-girl animations.' -ForegroundColor Green
Write-Host 'Balance display stays off until you build with your own secrets.h.' -ForegroundColor Yellow
