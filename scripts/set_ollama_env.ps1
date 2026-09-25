# Sets Ollama environment variables (VRAM-friendly settings) for this machine.

$vars = [ordered]@{
    OLLAMA_KEEP_ALIVE        = "0"
    OLLAMA_MAX_LOADED_MODELS = "3"          # router + embedder + one GPU worker
    OLLAMA_NUM_PARALLEL      = "1"
    OLLAMA_FLASH_ATTENTION   = "1"
    OLLAMA_KV_CACHE_TYPE     = "q8_0"
    OLLAMA_CONTEXT_LENGTH    = "4096"
    OLLAMA_GPU_OVERHEAD      = "536870912"
    OLLAMA_HOST              = "127.0.0.1:11434"
}

foreach ($name in $vars.Keys) {
    [Environment]::SetEnvironmentVariable($name, $vars[$name], "User")
    Write-Host ("{0} = {1}" -f $name, [Environment]::GetEnvironmentVariable($name, "User"))
}

Write-Host ""
Write-Host "Reminder: quit Ollama from the system tray, start it again, then run 'ollama ps' to confirm it is up."
