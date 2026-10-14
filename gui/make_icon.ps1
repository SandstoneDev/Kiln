<#
    make_icon.ps1 - draws Kiln's application icon (a plain kiln: a clay dome with a flame in its
    mouth) and writes it as a multi-size .ico. Called by Kiln.csproj before the compile, so the
    repository holds no binary icon file. Neutral art only: no game title, logo or game art.

    Usage: powershell -NoProfile -ExecutionPolicy Bypass -File make_icon.ps1 -Out <file.ico>
#>
param([Parameter(Mandatory = $true)][string]$Out)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

function New-RoundRect([float]$x, [float]$y, [float]$w, [float]$h, [float]$r) {
    $p = New-Object System.Drawing.Drawing2D.GraphicsPath
    $d = 2 * $r
    $p.AddArc($x, $y, $d, $d, 180, 90)
    $p.AddArc($x + $w - $d, $y, $d, $d, 270, 90)
    $p.AddArc($x + $w - $d, $y + $h - $d, $d, $d, 0, 90)
    $p.AddArc($x, $y + $h - $d, $d, $d, 90, 90)
    $p.CloseFigure()
    return $p
}

function Draw-Kiln([int]$n) {
    $bmp = New-Object System.Drawing.Bitmap($n, $n, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.Clear([System.Drawing.Color]::Transparent)
    $s = $n / 64.0

    # tile: dark slate
    $tile = New-RoundRect (1 * $s) (1 * $s) (62 * $s) (62 * $s) (12 * $s)
    $g.FillPath((New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 38, 42, 48))), $tile)

    # kiln body: a clay dome on a base
    $clay = [System.Drawing.Color]::FromArgb(255, 196, 112, 64)
    $clayDark = [System.Drawing.Color]::FromArgb(255, 150, 80, 44)
    $body = New-Object System.Drawing.Drawing2D.GraphicsPath
    $body.AddArc(10 * $s, 12 * $s, 44 * $s, 52 * $s, 180, 180)
    $body.AddLine(54 * $s, 38 * $s, 54 * $s, 52 * $s)
    $body.AddLine(54 * $s, 52 * $s, 10 * $s, 52 * $s)
    $body.CloseFigure()
    $g.FillPath((New-Object System.Drawing.SolidBrush($clay)), $body)
    $g.FillRectangle((New-Object System.Drawing.SolidBrush($clayDark)), 7 * $s, 50 * $s, 50 * $s, 6 * $s)

    # chimney
    $g.FillRectangle((New-Object System.Drawing.SolidBrush($clayDark)), 28 * $s, 6 * $s, 8 * $s, 9 * $s)

    # mouth (dark arch) with the flame
    $mouth = New-Object System.Drawing.Drawing2D.GraphicsPath
    $mouth.AddArc(22 * $s, 30 * $s, 20 * $s, 20 * $s, 180, 180)
    $mouth.AddLine(42 * $s, 40 * $s, 42 * $s, 50 * $s)
    $mouth.AddLine(42 * $s, 50 * $s, 22 * $s, 50 * $s)
    $mouth.CloseFigure()
    $g.FillPath((New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 30, 20, 16))), $mouth)

    $flame = New-Object System.Drawing.Drawing2D.GraphicsPath
    $flame.AddBezier(32 * $s, 33 * $s, 38 * $s, 40 * $s, 40 * $s, 46 * $s, 32 * $s, 50 * $s)
    $flame.AddBezier(32 * $s, 50 * $s, 24 * $s, 46 * $s, 26 * $s, 40 * $s, 32 * $s, 33 * $s)
    $flame.CloseFigure()
    $g.FillPath((New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 255, 176, 48))), $flame)
    $core = New-Object System.Drawing.Drawing2D.GraphicsPath
    $core.AddBezier(32 * $s, 40 * $s, 35 * $s, 44 * $s, 36 * $s, 47 * $s, 32 * $s, 50 * $s)
    $core.AddBezier(32 * $s, 50 * $s, 28 * $s, 47 * $s, 29 * $s, 44 * $s, 32 * $s, 40 * $s)
    $core.CloseFigure()
    $g.FillPath((New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 255, 236, 160))), $core)

    $g.Dispose()
    $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    return , $ms.ToArray()
}

$sizes = @(16, 20, 24, 32, 40, 48, 64, 128, 256)
$images = @()
foreach ($n in $sizes) { $images += , (Draw-Kiln $n) }

# ICONDIR + ICONDIRENTRY[] + PNG payloads (PNG-compressed entries: Windows Vista and later)
$fs = [System.IO.File]::Create($Out)
$w = New-Object System.IO.BinaryWriter($fs)
$w.Write([UInt16]0); $w.Write([UInt16]1); $w.Write([UInt16]$sizes.Count)
$offset = 6 + 16 * $sizes.Count
for ($i = 0; $i -lt $sizes.Count; $i++) {
    $n = $sizes[$i]; $len = $images[$i].Length
    $dim = if ($n -ge 256) { 0 } else { $n }
    $w.Write([byte]$dim); $w.Write([byte]$dim); $w.Write([byte]0); $w.Write([byte]0)
    $w.Write([UInt16]1); $w.Write([UInt16]32); $w.Write([UInt32]$len); $w.Write([UInt32]$offset)
    $offset += $len
}
foreach ($img in $images) { $w.Write($img) }
$w.Flush(); $w.Dispose()
Write-Host "icon: $Out ($($sizes.Count) sizes)"
