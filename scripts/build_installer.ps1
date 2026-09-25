# Full release build of the Tauri app into an unsigned .msi and NSIS .exe installer.
# Run from the repo root:  powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }

# --- a. verify the DEV machine's models -------------------------------------------------------
# This is a sanity check of the machine doing the build, so a broken setup is caught before it
# is packaged. It does NOT bundle models into the installer: that would add many GB and is not
# attempted. Each END USER machine still needs its own one-time setup (pull_models.py,
# pull_piper_voice.py, pull_whisper_model.py, Ollama, PostgreSQL, mpv).
Step "a. verifying local models"
$py = Join-Path $root "venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "venv python not found at $py" -ForegroundColor Red
    exit 1
}
& $py (Join-Path $root "scripts\verify_models.py")
if ($LASTEXITCODE -ne 0) {
    Write-Host "fix the above before building an installer that will ship a broken first-run experience" -ForegroundColor Red
    exit 1
}

# --- b. build ---------------------------------------------------------------------------------
Step "b. npm run tauri build (compiles Rust in release mode, then runs the bundlers)"
Set-Location (Join-Path $root "frontend-tauri")
npm run tauri build
if ($LASTEXITCODE -ne 0) {
    Write-Host "tauri build failed (exit code $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}

# --- c. locate the installers -----------------------------------------------------------------
Step "c. installers"
$bundle = Join-Path $root "frontend-tauri\src-tauri\target\release\bundle"
$msi = Get-ChildItem -Path (Join-Path $bundle "msi") -Filter *.msi -ErrorAction SilentlyContinue
$exe = Get-ChildItem -Path (Join-Path $bundle "nsis") -Filter *.exe -ErrorAction SilentlyContinue
if (-not $msi -and -not $exe) {
    Write-Host "the build succeeded but no installer was found under $bundle" -ForegroundColor Red
    exit 1
}
foreach ($f in @($msi) + @($exe)) {
    if ($f) { Write-Host $f.FullName -ForegroundColor Green }
}
Write-Host ""
Write-Host "unsigned installer - Windows SmartScreen will show 'unknown publisher' on first run; click 'More info' then 'Run anyway'. This is expected for an unsigned app."
