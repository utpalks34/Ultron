# Read-only environment check: prints "OK <version>" or "MISSING" per item.

function Check-Cmd {
    param([string]$Label, [string]$Exe, [string[]]$CmdArgs)
    if (Get-Command $Exe -ErrorAction SilentlyContinue) {
        try {
            $out = (& $Exe @CmdArgs 2>&1 | Select-Object -First 1 | Out-String).Trim()
            if ($out) { Write-Host "OK $Label $out" } else { Write-Host "OK $Label" }
        }
        catch {
            Write-Host "MISSING $Label"
        }
    }
    else {
        Write-Host "MISSING $Label"
    }
}

Check-Cmd "python" "python" @("--version")
Check-Cmd "node"   "node"   @("-v")
Check-Cmd "npm"    "npm"    @("-v")
Check-Cmd "rustc"  "rustc"  @("--version")
Check-Cmd "cargo"  "cargo"  @("--version")
Check-Cmd "git"    "git"    @("--version")
if (Get-Command psql -ErrorAction SilentlyContinue) {
    Check-Cmd "psql" "psql" @("--version")
}
else {
    $pgPsql = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\psql.exe" -ErrorAction SilentlyContinue |
        Sort-Object { [int]($_.Directory.Parent.Name -replace '\D', '') } -Descending |
        Select-Object -First 1
    if ($pgPsql) {
        $out = (& $pgPsql.FullName --version 2>&1 | Select-Object -First 1 | Out-String).Trim()
        Write-Host "OK psql $out (not on PATH: $($pgPsql.FullName))"
    }
    else {
        Write-Host "MISSING psql"
    }
}
Check-Cmd "ollama" "ollama" @("--version")
Check-Cmd "mpv"    "mpv"    @("--version")

if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $gpu = (& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>&1 | Out-String).Trim()
    if ($gpu) { Write-Host "OK gpu $gpu" } else { Write-Host "MISSING gpu" }
}
else {
    Write-Host "MISSING gpu (nvidia-smi)"
}

$ollamaVars = @(
    "OLLAMA_KEEP_ALIVE", "OLLAMA_MAX_LOADED_MODELS", "OLLAMA_NUM_PARALLEL",
    "OLLAMA_FLASH_ATTENTION", "OLLAMA_KV_CACHE_TYPE", "OLLAMA_CONTEXT_LENGTH",
    "OLLAMA_GPU_OVERHEAD", "OLLAMA_HOST"
)
foreach ($name in $ollamaVars) {
    $val = [Environment]::GetEnvironmentVariable($name, "User")
    if ([string]::IsNullOrEmpty($val)) { $val = [Environment]::GetEnvironmentVariable($name, "Machine") }
    if ([string]::IsNullOrEmpty($val)) { Write-Host "MISSING $name" } else { Write-Host "OK $name=$val" }
}

$svcs = @(Get-Service -Name "postgresql-x64-*" -ErrorAction SilentlyContinue)
if ($svcs.Count -eq 0) {
    Write-Host "MISSING postgresql-x64-* (service not found)"
}
foreach ($svc in $svcs) {
    if ($svc.Status -eq "Running") {
        Write-Host "OK $($svc.Name) Running"
    }
    else {
        Write-Host "MISSING $($svc.Name) (status: $($svc.Status))"
    }
}
