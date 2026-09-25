# Read-only database check: prints PASS/FAIL per step and stops at the first failure.
$ErrorActionPreference = "Stop"

# --- Locate psql ---
$psql = $null
$cmd = Get-Command psql -ErrorAction SilentlyContinue
if ($cmd) {
    $psql = $cmd.Source
} else {
    $pgRoot = "C:\Program Files\PostgreSQL"
    $best = $null
    if (Test-Path $pgRoot) {
        $best = Get-ChildItem $pgRoot -Directory |
            Where-Object { $_.Name -match '^\d+$' -and (Test-Path (Join-Path $_.FullName "bin\psql.exe")) } |
            Sort-Object { [int]$_.Name } -Descending |
            Select-Object -First 1
    }
    if ($best) {
        $psql = Join-Path $best.FullName "bin\psql.exe"
    } else {
        Write-Host "psql not found on PATH or under C:\Program Files\PostgreSQL\<version>\bin. Install PostgreSQL first." -ForegroundColor Red
        exit 1
    }
}
Write-Host "Using psql: $psql"

# --- Read PG_DSN from backend-ai\.env (never printed: it contains the password) ---
$envFile = Join-Path $PSScriptRoot "..\backend-ai\.env"
if (-not (Test-Path $envFile)) {
    Write-Host "FAIL .env not found at $envFile" -ForegroundColor Red
    exit 1
}
$dsnLine = Select-String -Path $envFile -Pattern '^\s*PG_DSN\s*=' | Select-Object -First 1
if (-not $dsnLine) {
    Write-Host "FAIL PG_DSN is not set in $envFile" -ForegroundColor Red
    exit 1
}
$dsn = (($dsnLine.Line -split '=', 2)[1]).Trim().Trim('"').Trim("'")

# Run one SQL statement through psql; returns @{ Code; Out; Err }.
function Invoke-Sql {
    param([string]$Sql)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $all = & $psql $dsn -v ON_ERROR_STOP=1 -tA -c $Sql 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    $err = (@($all | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } |
        ForEach-Object { $_.ToString() }) -join "`n").Trim()
    $out = @($all | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } |
        ForEach-Object { "$_" })
    return @{ Code = $code; Out = $out; Err = $err }
}

function Stop-Fail {
    param([string]$Label, [string]$Detail, [string]$Hint)
    Write-Host "FAIL $Label" -ForegroundColor Red
    if ($Detail) { Write-Host $Detail -ForegroundColor Red }
    if ($Hint) { Write-Host "Hint: $Hint" -ForegroundColor Yellow }
    exit 1
}

# --- a. connect ---
$r = Invoke-Sql "SELECT current_user, current_database(), version()"
if ($r.Code -ne 0) {
    if ($r.Err -match "password authentication failed") {
        Stop-Fail "connect" $r.Err "run scripts\setup_db.ps1"
    }
    Stop-Fail "connect" $r.Err "check that the PostgreSQL service is running and PG_DSN in backend-ai\.env is correct (run scripts\setup_db.ps1 if the role or database does not exist)"
}
Write-Host "PASS connect: $($r.Out -join ' ')" -ForegroundColor Green

# --- b. extensions ---
$r = Invoke-Sql "SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm')"
if ($r.Code -ne 0) { Stop-Fail "extensions" $r.Err $null }
$have = @($r.Out | ForEach-Object { $_.Trim() })
foreach ($ext in @("vector", "pg_trgm")) {
    if ($have -notcontains $ext) {
        $hint = "run scripts\setup_db.ps1"
        if ($ext -eq "vector") {
            $hint = "build pgvector for this PostgreSQL version: scripts\install_pgvector.bat"
        }
        Stop-Fail "extension $ext missing" $null $hint
    }
    Write-Host "PASS extension $ext present" -ForegroundColor Green
}

# --- c. tables ---
$tables = @("documents", "chunks", "episodes", "registry_apps", "registry_media", "registry_contacts")
$list = ($tables | ForEach-Object { "'$_'" }) -join ","
$r = Invoke-Sql "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name IN ($list)"
if ($r.Code -ne 0) { Stop-Fail "tables" $r.Err $null }
$found = @($r.Out | ForEach-Object { $_.Trim() })
foreach ($t in $tables) {
    if ($found -notcontains $t) {
        Stop-Fail "table $t missing" $null "run scripts\setup_db.ps1"
    }
    Write-Host "PASS table $t exists" -ForegroundColor Green
}

# --- d. registry counts ---
foreach ($t in @("registry_apps", "registry_media")) {
    $r = Invoke-Sql "SELECT count(*) FROM $t"
    if ($r.Code -ne 0) { Stop-Fail "count $t" $r.Err $null }
    Write-Host "PASS $t rows: $(($r.Out -join '').Trim())" -ForegroundColor Green
}

Write-Host "Database check complete." -ForegroundColor Green
