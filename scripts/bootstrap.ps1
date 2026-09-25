# Bootstraps the Ultron dev environment: Python venv, .env, backend deps, frontend deps.
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Backend  = Join-Path $RepoRoot "backend-ai"
$Frontend = Join-Path $RepoRoot "frontend-tauri"
$Venv     = Join-Path $Backend ".venv"
$VenvPy   = Join-Path $Venv "Scripts\python.exe"

function Assert-Ok($what) {
    # $ErrorActionPreference does not catch failing native commands, so check the exit code.
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

# 1. Virtual environment
if (-not (Test-Path $VenvPy)) {
    Write-Host "==> Creating venv at $Venv"
    py -3.11 -m venv $Venv
    Assert-Ok "venv creation"
} else {
    Write-Host "==> venv already exists"
}

# 2. .env
$EnvFile    = Join-Path $Backend ".env"
$EnvExample = Join-Path $Backend ".env.example"
if (-not (Test-Path $EnvFile)) {
    Copy-Item $EnvExample $EnvFile
    Write-Host "==> Created backend-ai\.env from .env.example"
    Write-Host "NOTE: edit backend-ai\.env and set MUSIC_FOLDER to your real music folder."
} else {
    Write-Host "==> backend-ai\.env already exists (left untouched)"
}

# 3. Python dependencies
# The voice / hands / vision extras are installed in later phases.
Write-Host "==> Upgrading pip"
& $VenvPy -m pip install --upgrade pip
Assert-Ok "pip upgrade"

Write-Host "==> Installing backend dependencies"
Push-Location $Backend
try {
    & $VenvPy -m pip install -e ".[dev]"
    Assert-Ok "pip install"
} finally {
    Pop-Location
}

# 4. Frontend dependencies
if (Test-Path (Join-Path $Frontend "package.json")) {
    Write-Host "==> npm install"
    Push-Location $Frontend
    try {
        npm install
        Assert-Ok "npm install"
    } finally {
        Pop-Location
    }
} else {
    Write-Host "package.json not found yet (created in Phase 1) - skipping npm"
}

Write-Host "bootstrap complete"
