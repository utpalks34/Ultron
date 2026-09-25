# Installs Ultron's toolchain via winget. Run from an elevated PowerShell.

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "This script must be run as Administrator. Right-click PowerShell, choose 'Run as administrator', and try again." -ForegroundColor Red
    exit 1
}

$common = @("--accept-package-agreements", "--accept-source-agreements")

function Install-Pkg {
    param(
        [string]$Id,
        [string]$Override = $null
    )
    Write-Host "==> Installing $Id"
    try {
        $wingetArgs = @("install", "--id", $Id, "-e") + $common
        if ($Override) { $wingetArgs += @("--override", $Override) }
        & winget @wingetArgs
        if ($LASTEXITCODE -ne 0) { throw "winget exited with code $LASTEXITCODE" }
        Write-Host "OK $Id" -ForegroundColor Green
    }
    catch {
        Write-Host "FAILED $Id : $_" -ForegroundColor Red
    }
}

Install-Pkg "Python.Python.3.11"
Install-Pkg "OpenJS.NodeJS.LTS"
Install-Pkg "Rustlang.Rustup"
Install-Pkg "Git.Git"
Install-Pkg "Ollama.Ollama"
Install-Pkg "Microsoft.VisualStudio.2022.BuildTools" "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
Install-Pkg "PostgreSQL.PostgreSQL.16" "--unattendedmodeui minimal --mode unattended --superpassword ultron_admin --servicename postgresql-x64-16 --serverport 5432"

Write-Host ""
Write-Host "==> mpv: searching winget (package id is not guessed)"
try {
    winget search mpv
}
catch {
    Write-Host "FAILED winget search mpv : $_" -ForegroundColor Red
}
Write-Host "NOTE: install the mpv package from the list above manually with: winget install --id <ID> -e" -ForegroundColor Yellow

Write-Host ""
Write-Host "Close this window and open a NEW PowerShell so PATH refreshes."
