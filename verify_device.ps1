param([string]$Port = 'COM4', [int]$TimeoutSeconds = 380)
$ErrorActionPreference = 'Stop'
$logDir = Join-Path $PSScriptRoot '.build'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$expected = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'generated\asset-report.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$lines = [System.Collections.Generic.List[string]]::new()
$cycles = [System.Collections.Generic.List[object]]::new()
$transitions = [System.Collections.Generic.List[object]]::new()
$testStarted = $false
$done = $false
$balanceSeen = $false
$devicePort = [System.IO.Ports.SerialPort]::new($Port, 115200)
$devicePort.DtrEnable = $false
$devicePort.RtsEnable = $false
$devicePort.ReadTimeout = 250
$devicePort.NewLine = "`n"
$timer = [System.Diagnostics.Stopwatch]::StartNew()
try {
    $devicePort.Open()
    $devicePort.DiscardInBuffer()
    $devicePort.Write("?`nT`n")
    while ($timer.Elapsed.TotalSeconds -lt $TimeoutSeconds -and !$done) {
        try { $line = $devicePort.ReadLine().Trim() }
        catch [System.TimeoutException] { continue }
        $lines.Add($line)
        Write-Output $line
        if ($line -match '\[error\]|Guru Meditation|panic|Brownout|stack overflow|abort\(|\[test\] cancelled') {
            throw "Device validation failed: $line"
        }
        if ($line -eq '[balance] updated' -or $line -match '\[status\].*balance=ready') { $balanceSeen = $true }
        if ($line -eq '[test] begin actions=30') { $testStarted = $true; continue }
        if ($testStarted -and $line -match '^\[transition\] index=(\d+) steps=(\d+) elapsed=(\d+) max_step=(\d+)$') {
            $index = [int]$Matches[1]; $steps = [int]$Matches[2]
            $elapsed = [int]$Matches[3]; $maxStep = [int]$Matches[4]
            if ($index -ne $transitions.Count -or $index -ne $cycles.Count -or $steps -ne 4 -or $elapsed -lt 150 -or $elapsed -gt 300 -or $maxStep -gt 83) {
                throw "Incorrect transition sequence/timing: $line"
            }
            $transitions.Add([PSCustomObject]@{ index=$index; steps=$steps; elapsed_ms=$elapsed; max_step_ms=$maxStep })
        }
        if ($testStarted -and $line -match '^\[cycle\] index=(\d+) id=(\w+) rendered=(\d+)/(\d+) elapsed=(\d+) target=(\d+) max_render=(\d+) errors=(\d+)$') {
            $index = [int]$Matches[1]; $id = $Matches[2]
            $rendered = [int]$Matches[3]; $total = [int]$Matches[4]
            $elapsed = [int]$Matches[5]; $target = [int]$Matches[6]
            $maxRender = [int]$Matches[7]; $errors = [int]$Matches[8]
            if ($index -ne $cycles.Count -or $index -ge 30) { throw "Unexpected sequence: $line" }
            $action = $expected.actions[$index]
            if ($id -ne $action.id -or $rendered -ne $action.frames -or $total -ne $action.frames -or $errors -ne 0) {
                throw "Incomplete/corrupt action: $line"
            }
            if ($target -ne $action.duration_ms -or $elapsed -lt $target -or $elapsed -gt $target + 1500) {
                throw "Incorrect playback duration: $line"
            }
            if ($maxRender -gt 83) { throw "Frame renderer exceeded the 12fps frame budget: $line" }
            $cycles.Add([PSCustomObject]@{ index=$index; id=$id; rendered=$rendered; total=$total; elapsed_ms=$elapsed; target_ms=$target; max_render_ms=$maxRender; errors=$errors })
        }
        if ($line -eq '[test] complete actions=30 errors=0') {
            if (!$testStarted -or $cycles.Count -ne 30 -or $transitions.Count -ne 30) { throw 'Device reported completion without 30 verified actions and transitions' }
            $done = $true
        }
    }
    if (!$done) { throw 'Timed out before all 30 complete actions were verified' }
    if (!$balanceSeen) { throw 'All animations passed, but balance display was never ready' }
    [PSCustomObject]@{ port=$Port; revision='7.1-fixed-camera'; result='PASS'; actions=$cycles.Count; rendered_frames=($cycles.ToArray() | Measure-Object -Property rendered -Sum).Sum; balance_ready=$balanceSeen; cycles=$cycles.ToArray(); transitions=$transitions.ToArray(); camera_scale=$expected.camera_scale; target_anchor=$expected.target_anchor; screen_y=$expected.screen_y; asset_pack_sha256=$expected.pack_sha256 } |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $logDir 'device-validation-v7_1.json') -Encoding UTF8
    Write-Output 'PASS: 30 actions, 3600 frames, 30 smooth transitions, 0 CRC/JPEG errors, balance ready.'
} finally {
    if ($devicePort.IsOpen) { $devicePort.Close() }
    $devicePort.Dispose()
    [System.IO.File]::WriteAllLines((Join-Path $logDir 'device-validation-v7_1.log'), $lines.ToArray(), [System.Text.UTF8Encoding]::new($false))
}
