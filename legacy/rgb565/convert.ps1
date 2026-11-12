# Whale girl PNG -> RGB565 C array
Add-Type -AssemblyName System.Drawing

$srcPath = Join-Path $PSScriptRoot 'assets\whale-girl.png'
$outPath = Join-Path $PSScriptRoot 'whale_girl.h'

$src = [System.Drawing.Image]::FromFile($srcPath)
$W = $src.Width
$H = $src.Height
Write-Host "source: $W x $H"

# scale to height 96, keep ratio
$th = 96
$tw = [int]([double]$W * $th / $H)
if ($tw -gt 110) { $tw = 110 }

$scaled = New-Object System.Drawing.Bitmap($tw, $th)
$g = [System.Drawing.Graphics]::FromImage($scaled)
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
$g.DrawImage($src, 0, 0, $tw, $th)
$g.Dispose()
$src.Dispose()

# crop center square 96x96 if wider
if ($tw -gt 96) {
    $x0 = [int](($tw - 96) / 2)
    $crop = New-Object System.Drawing.Bitmap(96, 96)
    $g2 = [System.Drawing.Graphics]::FromImage($crop)
    $g2.DrawImage($scaled,
        (New-Object System.Drawing.Rectangle(0, 0, 96, 96)),
        (New-Object System.Drawing.Rectangle($x0, 0, 96, 96)),
        [System.Drawing.GraphicsUnit]::Pixel)
    $g2.Dispose()
    $scaled.Dispose()
    $scaled = $crop
    $tw = 96
}

# build pixel array
$count = $tw * $th
$pixels = New-Object System.Collections.Generic.List[string]
for ($y = 0; $y -lt $th; $y++) {
    for ($x = 0; $x -lt $tw; $x++) {
        $c = $scaled.GetPixel($x, $y)
        if ($c.A -lt 128) {
            $pixels.Add("0x0000")
        } else {
            $r5 = [int]$c.R -shr 3
            $g6 = [int]$c.G -shr 2
            $b5 = [int]$c.B -shr 3
            $v = ($r5 -shl 11) -bor ($g6 -shl 5) -bor $b5
            $pixels.Add(("0x{0:X4}" -f $v))
        }
    }
}
$scaled.Dispose()

# write header file
$sb = New-Object System.Text.StringBuilder
[void]$sb.AppendLine("const uint16_t whaleGirl[$count] PROGMEM = {")
for ($i = 0; $i -lt $pixels.Count; $i += 12) {
    $end = [Math]::Min($i + 11, $pixels.Count - 1)
    $line = ($pixels[$i..$end] -join ",")
    [void]$sb.AppendLine("$line,")
}
[void]$sb.AppendLine("};")
[void]$sb.AppendLine("#define WHALE_W $tw")
[void]$sb.AppendLine("#define WHALE_H $th")
[System.IO.File]::WriteAllText($outPath, $sb.ToString())

Write-Host "output: $tw x $th, $count pixels"
Write-Host "saved: $outPath"
