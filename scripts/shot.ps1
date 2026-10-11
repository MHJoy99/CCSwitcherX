<#
.SYNOPSIS
  Render an HTML or SVG file to a PNG with headless Microsoft Edge (a development helper; nothing is shown on screen).
.EXAMPLE
  pwsh scripts/shot.ps1 assets/hero-banner.svg out.png -Width 1280 -Height 400 -WaitMs 2500
#>
param(
  [Parameter(Mandatory)][string]$Path,
  [Parameter(Mandatory)][string]$Out,
  [int]$Width = 1280,
  [int]$Height = 720,
  [int]$WaitMs = 1500,     # lets CSS/SMIL animations advance before the picture is taken
  [switch]$Dark,           # force prefers-color-scheme: dark
  [switch]$Transparent,
  [double]$At = -1,        # SVG only: freeze every animation (CSS and SMIL) at exactly this many seconds, then capture. Deterministic; use this to review a loop.
  [switch]$Inline,         # SVG only: embed it in the page so -WaitMs really advances its animation (the default <img> mode shows frame 0)
  [string]$Bg = '#ffffff'  # page colour behind an SVG (README pages are white or dark grey; check both)
)
$edge = @(
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { throw 'Microsoft Edge was not found.' }

$full = (Resolve-Path $Path).Path
$outFull = [System.IO.Path]::GetFullPath($Out)
New-Item -ItemType Directory -Force -Path (Split-Path $outFull) | Out-Null
$profileDir = Join-Path $env:TEMP ("ccsx-edge-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$wrapper = $null
if ($At -ge 0) { $Inline = $true }
if ($full -like '*.svg') {
  # Show the SVG the way a GitHub README does: inside an <img> (scripts disabled, CSS/SMIL animations run).
  $wrapper = Join-Path $env:TEMP ("ccsx-wrap-" + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.html')
  $svgUrl = ([System.Uri]$full).AbsoluteUri
  if ($Inline) {
    $markup = (Get-Content -Raw -Encoding UTF8 $full) -replace '^\s*<\?xml[^>]*\?>', ''
    $freeze = ''
    if ($At -ge 0) {
      $sec = $At.ToString([System.Globalization.CultureInfo]::InvariantCulture)
      $freeze = "<script>addEventListener('load',function(){var t=$sec;document.querySelectorAll('svg').forEach(function(s){try{s.pauseAnimations();s.setCurrentTime(t)}catch(e){}});document.getAnimations().forEach(function(a){a.pause();a.currentTime=t*1000})})</script>"
    }
    Set-Content -Path $wrapper -Encoding UTF8 -Value "<!doctype html><meta charset=utf-8><body style=""margin:0;background:$Bg""><div style=""width:100%"">$markup</div><style>svg{display:block;width:100%;height:auto}</style>$freeze"
  } else {
    Set-Content -Path $wrapper -Encoding UTF8 -Value "<!doctype html><meta charset=utf-8><body style=""margin:0;background:$Bg""><img src=""$svgUrl"" style=""display:block;width:100%"">"
  }
  $url = ([System.Uri]$wrapper).AbsoluteUri
} else {
  $url = ([System.Uri]$full).AbsoluteUri
}

$flags = @(
  '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
  "--user-data-dir=$profileDir", "--window-size=$Width,$Height", "--virtual-time-budget=$WaitMs",
  "--screenshot=$outFull", $url
)
if ($Dark) { $flags = @('--force-dark-mode', '--blink-settings=preferredColorScheme=0') + $flags }
if ($Transparent) { $flags = @('--default-background-color=00000000') + $flags }

$p = Start-Process -FilePath $edge -ArgumentList $flags -PassThru -WindowStyle Hidden
if (-not $p.WaitForExit(60000)) { $p.Kill($true); Remove-Item $profileDir -Recurse -Force -ErrorAction SilentlyContinue; throw 'Edge timed out.' }
Remove-Item $profileDir -Recurse -Force -ErrorAction SilentlyContinue
if ($wrapper) { Remove-Item $wrapper -Force -ErrorAction SilentlyContinue }
if (-not (Test-Path $outFull)) { throw "No screenshot was written to $outFull" }
Write-Output "wrote $outFull ($((Get-Item $outFull).Length) bytes)"
