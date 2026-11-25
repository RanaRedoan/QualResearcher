# Installs the Hind Siliguri Bangla font for the current Windows user (no admin rights needed),
# so Word shows QualResearcher's transcripts in the same font as the app.
$src  = Join-Path $PSScriptRoot "fonts"
$dest = Join-Path $env:LOCALAPPDATA "Microsoft\Windows\Fonts"
$reg  = "HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
if (-not (Test-Path $reg)) { New-Item -Path $reg -Force | Out-Null }
Get-ChildItem $src -Filter "*.ttf" | ForEach-Object {
    $target = Join-Path $dest $_.Name
    if (-not (Test-Path $target)) { Copy-Item $_.FullName $target }
    $style = $_.BaseName -replace "HindSiliguri-", ""
    New-ItemProperty -Path $reg -Name "Hind Siliguri $style (TrueType)" -Value $target -PropertyType String -Force | Out-Null
}
Write-Host "Hind Siliguri font installed. Restart Word if it is open."
