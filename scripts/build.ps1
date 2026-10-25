$ErrorActionPreference = 'Stop'
$polyRoot = Split-Path -Parent $PSScriptRoot
$polyBase = Split-Path -Parent $polyRoot
$env:TEMP = Join-Path $polyBase 'work'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $polyBase 'work\pip-cache'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $polyBase 'work\pyinstaller-cache'
$polyPython = Join-Path $polyBase '.venv\Scripts\python.exe'
$polyRuntime = Split-Path -Parent (& $polyPython -c 'import sys; print(sys._base_executable)')
$polyVersion = & $polyPython -c "import sys; sys.path.insert(0, r'$polyRoot'); from poly5 import __version__; print(__version__)"
$polyDist = [System.IO.Path]::GetFullPath((Join-Path $polyBase "outputs\$polyVersion"))
$env:POLY5_DIST_ROOT = $polyDist
if (-not $polyDist.StartsWith([System.IO.Path]::GetFullPath($polyBase) + '\')) { throw 'Build target outside project storage root' }
$polyOriginalPath = $env:PATH
# Prevent unrelated tools on PATH (e.g. Poppler's versioned ICU) from being
# mistaken for Qt's Windows system ICU dependency by PyInstaller.
$env:PATH = "$(Join-Path $polyBase '.venv\Scripts');$polyRuntime;C:\Windows\System32;C:\Windows"
Push-Location $polyRoot
try {
    & $polyPython scripts/generate_assets.py
    if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed' }
    & $polyPython -m PyInstaller --noconfirm --windowed --onedir --name Poly5 --icon (Join-Path $polyRoot 'assets\poly5.ico') --version-file (Join-Path $polyRoot 'scripts\version_info.txt') --add-data "$(Join-Path $polyRoot 'assets\orderbook-roi-v1.json');assets" --collect-data polymarket --copy-metadata polymarket-client --copy-metadata scikit-learn --distpath $polyDist --workpath (Join-Path $polyBase "work\build-$polyVersion") --specpath (Join-Path $polyBase 'work') main.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
    & $polyPython scripts/stage_release.py
    if ($LASTEXITCODE -ne 0) { throw 'Release documentation staging failed' }
    & $polyPython -m pip freeze | Out-File -LiteralPath (Join-Path $polyDist 'Poly5\requirements-lock.txt') -Encoding utf8
    Get-FileHash -LiteralPath (Join-Path $polyDist 'Poly5\Poly5.exe') -Algorithm SHA256 | ConvertTo-Json | Out-File -LiteralPath (Join-Path $polyDist 'Poly5\exe-sha256.json') -Encoding utf8
} finally { $env:PATH = $polyOriginalPath; Pop-Location }

