$ErrorActionPreference='Stop'
$trainerSource=Split-Path -Parent $PSScriptRoot
$trainerBase=Split-Path -Parent $trainerSource
$env:TEMP=Join-Path $trainerBase 'work'
$env:TMP=$env:TEMP
$env:PYINSTALLER_CONFIG_DIR=Join-Path $trainerBase 'work\trainer-pyinstaller-cache'
$trainerPython=Join-Path $trainerBase '.venv\Scripts\python.exe'
$trainerVersion=& $trainerPython -c 'import runpy,sys;print(runpy.run_path(sys.argv[1])["__version__"])' (Join-Path $trainerSource 'trainstation\__init__.py')
if($LASTEXITCODE -ne 0){throw 'Cannot read trainer version'}
$trainerDist=Join-Path $trainerBase "outputs\POLY5Trainer-$trainerVersion"
$trainerOriginalPath=$env:PATH
$trainerRuntime=Split-Path -Parent (& $trainerPython -c 'import sys;print(sys._base_executable)')
$env:PATH="$(Join-Path $trainerBase '.venv\Scripts');$trainerRuntime;C:\Windows\System32;C:\Windows"
Push-Location $trainerSource
try{
  & $trainerPython -m PyInstaller --noconfirm --windowed --onedir --name POLY5Trainer --icon (Join-Path $trainerSource 'assets\poly5.ico') --add-data "$(Join-Path $trainerSource 'assets\poly5.ico');assets" --add-data "$(Join-Path $trainerSource 'trainstation\seed_data');trainstation/seed_data" --collect-data polymarket --copy-metadata polymarket-client --copy-metadata scikit-learn --collect-all polars --distpath $trainerDist --workpath (Join-Path $trainerBase "work\build-trainer-$trainerVersion") --specpath (Join-Path $trainerBase 'work') trainer_main.py
  if($LASTEXITCODE -ne 0){throw 'Trainer EXE build failed'}
  Copy-Item -LiteralPath (Join-Path $trainerSource 'TRAINER_README.md') -Destination (Join-Path $trainerDist 'POLY5Trainer\README.txt')
  & $trainerPython -m pip freeze | Out-File -LiteralPath (Join-Path $trainerDist 'POLY5Trainer\requirements-lock.txt') -Encoding utf8
  if($LASTEXITCODE -ne 0){throw 'Cannot record trainer dependencies'}
  & $trainerPython (Join-Path $trainerSource 'scripts\trainer_release.py') stage (Join-Path $trainerDist 'POLY5Trainer')
  if($LASTEXITCODE -ne 0){throw 'Trainer payload verification failed'}
}finally{$env:PATH=$trainerOriginalPath;Pop-Location}
