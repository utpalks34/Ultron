param(
    [string]$SuperPassword,
    [int]$PgPort = 5432
)

$ErrorActionPreference = "Stop"

# --- Read PG_DSN from backend-ai\.env (the password is never printed) ---
$envFile = Join-Path $PSScriptRoot "..\backend-ai\.env"
if (-not (Test-Path $envFile)) {
    Write-Host ".env not found at $envFile. Create backend-ai\.env with PG_DSN first." -ForegroundColor Red
    exit 1
}
$dsnLine = Select-String -Path $envFile -Pattern '^\s*PG_DSN\s*=' | Select-Object -First 1
if (-not $dsnLine) {
    Write-Host "PG_DSN is not set in $envFile." -ForegroundColor Red
    exit 1
}
$dsn = (($dsnLine.Line -split '=', 2)[1]).Trim().Trim('"').Trim("'")
if ($dsn -notmatch '^postgres(?:ql)?://([^:@/]+):([^@]*)@([^:/]+)(?::(\d+))?/([^?]*)') {
    Write-Host "PG_DSN in $envFile does not match postgresql://<user>:<password>@<host>:<port>/<db>." -ForegroundColor Red
    exit 1
}
$dsnUser = [uri]::UnescapeDataString($Matches[1])
$UltronPassword = [uri]::UnescapeDataString($Matches[2])
$DbName = [uri]::UnescapeDataString($Matches[5])
if ([string]::IsNullOrEmpty($DbName)) { $DbName = "ultron" }
$UltronPasswordSql = $UltronPassword.Replace("'", "''")
$DbNameLiteral = $DbName.Replace("'", "''")
$DbNameIdent = '"' + $DbName.Replace('"', '""') + '"'

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

$schemaFile = Join-Path $PSScriptRoot "..\backend-ai\memory\schema.sql"
if (-not (Test-Path $schemaFile)) {
    Write-Host "schema.sql not found at $schemaFile" -ForegroundColor Red
    exit 1
}

# --- Superuser password (never printed) ---
if ([string]::IsNullOrEmpty($SuperPassword)) {
    $secure = Read-Host "postgres superuser password" -AsSecureString
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $SuperPassword = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

# Run SQL (piped via stdin) through psql; stop the script on any failure.
function Invoke-Psql {
    param(
        [string]$User,
        [string]$Password,
        [string]$Database,
        [string]$Sql,
        [string[]]$Flags = @(),
        [string]$Step
    )
    $env:PGPASSWORD = $Password
    # Native stderr must not terminate the script here, so it can be inspected.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $all = $Sql | & $psql -h 127.0.0.1 -p $PgPort -U $User -d $Database -v ON_ERROR_STOP=1 @Flags 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prevEap

    $errText = (@($all | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } |
        ForEach-Object { $_.ToString() }) -join "`n").Trim()
    $out = @($all | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] })

    if ($code -ne 0) {
        if ($errText) { Write-Host $errText -ForegroundColor Red }
        Write-Host "FAILED: $Step (psql exit code $code)" -ForegroundColor Red
        if ($User -eq "postgres" -and $errText -match "password authentication failed") {
            Write-Host "The postgres superuser password is the one you set when installing PostgreSQL. Re-run and enter it." -ForegroundColor Yellow
        }
        exit 1
    }
    return $out
}

# --- Role ---
$roleExists = Invoke-Psql -User postgres -Password $SuperPassword -Database postgres `
    -Sql "SELECT 1 FROM pg_roles WHERE rolname='ultron'" -Flags @("-tA") -Step "check role ultron"
if (-not ($roleExists -match "1")) {
    Write-Host "Creating role ultron..."
    Invoke-Psql -User postgres -Password $SuperPassword -Database postgres `
        -Sql "CREATE ROLE ultron LOGIN PASSWORD '$UltronPasswordSql';" -Step "create role ultron" | Out-Null
} else {
    Write-Host "Role ultron already exists."
    Invoke-Psql -User postgres -Password $SuperPassword -Database postgres `
        -Sql "ALTER ROLE ultron WITH LOGIN PASSWORD '$UltronPasswordSql';" -Step "sync role ultron password" | Out-Null
    Write-Host "Role ultron password synced from .env"
}

# --- Database ---
$dbExists = Invoke-Psql -User postgres -Password $SuperPassword -Database postgres `
    -Sql "SELECT 1 FROM pg_database WHERE datname='$DbNameLiteral'" -Flags @("-tA") -Step "check database $DbName"
if (-not ($dbExists -match "1")) {
    Write-Host "Creating database $DbName..."
    Invoke-Psql -User postgres -Password $SuperPassword -Database postgres `
        -Sql "CREATE DATABASE $DbNameIdent OWNER ultron;" -Step "create database $DbName" | Out-Null
} else {
    Write-Host "Database $DbName already exists."
}

# --- Extensions (superuser) ---
Write-Host "Creating extensions..."
Invoke-Psql -User postgres -Password $SuperPassword -Database $DbName `
    -Sql @'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
'@ -Step "create extensions vector/pg_trgm" | Out-Null

# --- Schema (as ultron) ---
Write-Host "Applying schema.sql..."
$env:PGPASSWORD = $UltronPassword
& $psql -h 127.0.0.1 -p $PgPort -U ultron -d $DbName -v ON_ERROR_STOP=1 -f $schemaFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAILED: apply schema.sql (psql exit code $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}

# --- Report ---
Write-Host "Tables and extensions:"
$env:PGPASSWORD = $UltronPassword
@'
\dt
\dx
'@ | & $psql -h 127.0.0.1 -p $PgPort -U ultron -d $DbName -v ON_ERROR_STOP=1
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAILED: list tables/extensions (psql exit code $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}
Write-Host "Database setup complete." -ForegroundColor Green
