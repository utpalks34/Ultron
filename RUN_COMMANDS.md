# Ultron - Run Commands

## Phase 0.1 - Scaffold

```powershell
# Go to the repo root
cd <path to ultron>
# Initialise git (skip if already a repo)
git init
# List every file to compare against the scaffold tree
Get-ChildItem -Recurse -File | Select-Object -ExpandProperty FullName
```

Done when: the file listing matches the tree above and there is no src-tauri folder.

## Phase 0.2 - Environment

**Step a - (Admin PowerShell)**
```powershell
# Go to the repo root
cd <path to ultron>
# Install Python, Node, Rust, Git, Ollama, VS Build Tools and PostgreSQL 16 via winget
.\scripts\install_tools.ps1
```

**Step b** - Close the window and open a NEW Admin PowerShell so PATH refreshes.

**Step c** - Install mpv manually, using the package id from the `winget search mpv` list printed in step a:
```powershell
# Install mpv (replace <ID> with the package id from the search results)
winget install --id <ID> -e
```

**Step d - (Admin "x64 Native Tools Command Prompt for VS 2022")**
```bat
:: Go to the repo root
cd <path to ultron>
:: Build and install pgvector into PostgreSQL 16
scripts\install_pgvector.bat
```

**Step e - (normal PowerShell)**
```powershell
# Go to the repo root
cd <path to ultron>
# Set the Ollama user environment variables
.\scripts\set_ollama_env.ps1
# Then quit Ollama from the system tray and start it again
```

**Step f**
```powershell
# Pull qwen2.5:1.5b-instruct, nomic-embed-text, qwen2.5-coder:3b and moondream
python scripts\pull_models.py
```

**Step g** - Reboot the machine.

**Step h - (after reboot)**
```powershell
# Go to the repo root
cd <path to ultron>
# Check every tool, the GPU, the Ollama variables and the PostgreSQL service
powershell -ExecutionPolicy Bypass -File scripts\check_env.ps1
```

Done when: check_env.ps1 shows OK for every line, nvidia-smi reports 4096 MiB, and `ollama run qwen2.5:1.5b-instruct "say hi"` replies.

## Phase 1 - Hollow bridge

**Step a - database setup and check (normal PowerShell)**
```powershell
# Go to the repo root
cd <path to ultron>
# Run once: creates role ultron / database ultron and applies schema.sql (prompts for the postgres password)
.\scripts\setup_db.ps1
# Check every tool, the GPU, the Ollama variables and the PostgreSQL service
powershell -ExecutionPolicy Bypass -File scripts\check_env.ps1
# Check the database connection, extensions, tables and registry counts
powershell -ExecutionPolicy Bypass -File scripts\check_db.ps1
```

**Step b - backend (PowerShell)**
```powershell
# Go to the backend folder (config.py reads .env from here)
cd <path to ultron>\backend-ai
# Activate the root virtualenv (Python 3.11)
..\venv\Scripts\Activate.ps1
# Start uvicorn on 127.0.0.1:8765, WebSocket at /ws?token=<SESSION_TOKEN>
python orchestrator.py
```

**Step c - frontend (second PowerShell window)**
```powershell
# Go to the frontend folder
cd <path to ultron>\frontend-tauri
# Install dependencies (first time only)
npm install
# Browser dev server, opens the HUD page with token "dev"
npm run dev
# Or the Tauri window (needs src-tauri\icons, create with: npx tauri icon <png>)
npm run tauri dev
```

Done when: the page shows "connected", the "state events" counter counts up, typing a message echoes an assistant line, and stopping the backend shows the red "backend offline - reconnecting" banner with reconnect attempts in the console.
