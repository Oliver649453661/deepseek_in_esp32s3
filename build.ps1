param(
    [switch]$Upload,
    [switch]$RebuildAssets,
    [string]$Port = 'COM4',
    [string]$Cli = '',
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$fqbn = 'esp32:esp32:esp32s3:FlashSize=16M,PartitionScheme=custom'
$buildPath = Join-Path $PSScriptRoot '.build'
$assetPath = Join-Path $PSScriptRoot 'generated\pet-assets.bin'
if (!$Cli) {
    if ($env:ARDUINO_CLI) { $Cli = $env:ARDUINO_CLI }
    else {
        $command = Get-Command arduino-cli -ErrorAction SilentlyContinue
        if ($command) { $Cli = $command.Source }
        else {
            $candidates = @(
                (Join-Path $env:LOCALAPPDATA 'Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe'),
                (Join-Path $env:ProgramFiles 'Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe'),
                'D:\ESP_IDE\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe'
            )
            $Cli = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        }
    }
}
if (!$Cli -or !(Test-Path -LiteralPath $Cli)) { throw 'Arduino CLI not found. Add it to PATH, set ARDUINO_CLI, or pass -Cli with its absolute path.' }
if (!(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'secrets.h'))) {
    throw 'Missing secrets.h. Copy secrets.example.h to secrets.h and fill in your local WiFi/API credentials.'
}
if ($RebuildAssets -or !(Test-Path -LiteralPath $assetPath) -or !(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'pet_assets.h'))) {
    & $Python -X utf8 (Join-Path $PSScriptRoot 'tools\build_pet_assets.py') --quality 65
    if ($LASTEXITCODE -ne 0) { throw "Asset build failed: $LASTEXITCODE" }
}
if ((Get-Item -LiteralPath $assetPath).Length -gt 14548992) { throw 'Animation pack exceeds its Flash partition' }
& $Cli compile --fqbn $fqbn --build-property 'upload.maximum_size=2097152' --build-path $buildPath $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw "Compile failed: $LASTEXITCODE" }
if ($Upload) {
    # Upload firmware, partition table and animations in one esptool transaction/reset.
    & $Cli upload --port $Port --fqbn $fqbn --input-dir $buildPath --upload-property "upload.extra_flags=0x210000 `"$assetPath`"" $PSScriptRoot
    if ($LASTEXITCODE -ne 0) { throw "Upload failed: $LASTEXITCODE" }
}
