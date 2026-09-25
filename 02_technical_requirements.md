# Ultron — Technical Requirements Document (TRD)

Companion to the Product Requirements Document. Defines the stack, hardware envelope, and measurable non-functional requirements the architecture must satisfy.

---

## 1. Target hardware

| Component | Specification |
| --- | --- |
| GPU | NVIDIA GTX 1650, 4 GB GDDR6 (Samsung memory, ~192 GB/s bandwidth) |
| System RAM | 16 GB |
| OS | Windows 10/11 |
| Storage | 60 GB free NVMe/SSD minimum (models + Postgres + Chromium profile) |
| CPU | 6+ cores recommended (STT, gestures, Postgres, Piper all run on CPU) |
| Microphone / speakers | Required for voice features (FR-5.1, FR-5.2) |

The "7 GB shared VRAM" some Windows systems report for this GPU is system RAM accessed over PCIe, not usable graphics memory in the sense Ollama allocates against. **Plan against 4,096 MB of real VRAM, minus a ~700 MB reserve for the desktop compositor and a headful Chromium instance**, leaving a working budget of approximately 3,300 MB.

## 2. Technology stack

| Layer | Technology | Notes |
| --- | --- | --- |
| Desktop shell | Tauri (Rust) | Native window, tray, global hotkey; transparent/undecorated window |
| Frontend UI | React 18 | Composition root, hooks-based state |
| 3D rendering | React Three Fiber (Three.js) | Orb mesh, 3D agent node graph, postprocessing (bloom) |
| Styling | Tailwind CSS | Glassmorphism utility layer |
| Frontend state | Zustand | Single store; high-frequency values (audio amplitude) bypass React state entirely |
| Backend framework | Python 3.11, FastAPI | WebSocket endpoint, lifespan-managed resources |
| Orchestration | LangGraph (Supervisor–Worker) | `StateGraph`, `astream_events` v2, Postgres checkpointer |
| LLM runtime | Ollama | Local model serving, `OLLAMA_KEEP_ALIVE` per-request control |
| Database | PostgreSQL 16 + `pgvector` extension | Documents, chunks, episodic memory, LangGraph checkpoints, action registries |
| DB admin | pgAdmin 4 | Manual inspection, saved diagnostic queries |
| STT | `faster-whisper` (CTranslate2) | CPU, `base.en`, int8 |
| VAD | Silero VAD | End-of-utterance detection |
| TTS | Piper | Streaming synthesis, CPU |
| Gestures | OpenCV + MediaPipe Hands | Optional, CPU, off by default |
| Browser automation | Playwright (Chromium, persistent context) | Headful, accessibility-tree targeting |
| OS automation | `pyautogui`, `subprocess`, `pygetwindow` | Window-verified clicks; CLI/protocol handlers preferred over GUI automation |
| Media playback | `mpv` (JSON IPC) | Local music library, transport control |
| Fuzzy matching | `rapidfuzz` | Registry alias resolution |

## 3. Model tier requirements (4 GB VRAM budget)

| Tier | Role | Model | Quant | Footprint | Device | `keep_alive` |
| --- | --- | --- | --- | --- | --- | --- |
| Router | Intent routing, CRAG grading | `qwen2.5:1.5b-instruct` | Q4_K_M | 1.1 GB | **CPU** (`num_gpu=0`) | `-1` (resident) |
| Embedder | pgvector embeddings | `nomic-embed-text` | F16 | 0.3 GB | **CPU** | `-1` (resident) |
| STT | Transcription | `faster-whisper base.en` | int8 | 0.4 GB | **CPU** | resident |
| TTS | Speech synthesis | Piper `en_GB-alba-medium` | — | 0.1 GB | **CPU** | resident |
| Worker | Dev / Web / OS / RAG reasoning | `qwen2.5-coder:3b` (or `llama3.2:3b`) | Q4_K_M | 2.2 GB | **GPU**, exclusive | `0` (evicted on task end) |
| Vision (optional) | Terminal screenshot reading | `moondream` (1.8B) | Q4 | 1.8 GB | **GPU**, exclusive, never concurrent with worker | `0` |

**Hard rule:** at most one non-CPU model resident on the GPU at any time. This is a functional requirement of the model manager, not a suggestion — exceeding it on this card causes partial layer offload and a 5–10x throughput drop.

**Rejected for this hardware:** any 7B-class model in the interactive agent loop. A 7B Q4_K_M model splits across GPU/CPU on 4 GB VRAM and runs at 4–6 tokens/second, which is unusable across a multi-tool-call agent turn. 7B+ models may be exposed later as an explicit, user-invoked "deep answer" path outside the normal graph.

## 4. Ollama runtime configuration

```bash
OLLAMA_KEEP_ALIVE=0             # default: evict immediately
OLLAMA_MAX_LOADED_MODELS=3      # CPU router + CPU embedder + one GPU worker
OLLAMA_NUM_PARALLEL=1           # no duplicated KV cache
OLLAMA_FLASH_ATTENTION=1        # supported on Turing (CC 7.5)
OLLAMA_KV_CACHE_TYPE=q8_0       # ~halves KV cache VRAM; requires flash attention
OLLAMA_CONTEXT_LENGTH=4096      # 8k context will not fit beside a 3B on 4 GB
OLLAMA_GPU_OVERHEAD=536870912   # 512 MB reserved for compositor + Chromium
OLLAMA_HOST=127.0.0.1:11434     # loopback only, never 0.0.0.0
```

## 5. RAM budget (16 GB system)

| Consumer | Budget | Note |
| --- | --- | --- |
| Windows + background | 3.5 GB | Assumes a browser tab and Explorer already open |
| Python backend process | 2.5 GB | whisper + MediaPipe + Playwright driver + asyncpg |
| Ollama CPU-resident models | 1.9 GB | router + embedder + model overhead |
| Chromium (headful, Playwright) | 1.5 GB | Cap at 3 open tabs |
| PostgreSQL | 1.2 GB | `shared_buffers = 1GB`, not the standard 25%-of-RAM default |
| Tauri + WebView2 (production build) | 0.6 GB | Dev server adds ~1 GB more; do not run both simultaneously |
| **Total** | **~11.2 GB** | ~4.8 GB slack |

## 6. Non-functional requirements

### 6.1 Performance

| Requirement | Target |
| --- | --- |
| Worker sustained generation rate | ≥ 30 tokens/sec (measured via `ollama run --verbose`) |
| Cold worker load time (NVMe) | ≤ 3 s, visibly communicated in UI within 300 ms of trigger |
| Router routing decision latency | ≤ 1 s on 6 CPU cores |
| STT transcription latency (5 s utterance) | ≤ 1 s (`base.en`, CPU) |
| WebSocket event propagation (publish → UI render) | ≤ 1 frame (~16 ms) under normal load |
| Peak dedicated VRAM (any single task) | < 2,900 MB |
| GPU idle-to-idle return after a task | Within 2 s of task completion |

### 6.2 Reliability / availability

- The system must recover to a working idle state after any single agent node throws — errors are surfaced, not fatal to the process.
- A dropped or stalled WebSocket must auto-reconnect with exponential backoff (capped at 5 s) and resynchronize state via a single `snapshot` event, never a replayed event log.
- Graph loop protection: a hard hop ceiling (`MAX_HOPS`, default 8) and a per-agent tool-call recursion limit (default 12) prevent runaway loops on a small, occasionally unreliable router model.

### 6.3 Security / privacy

- All network binding is loopback-only (`127.0.0.1`); the WebSocket requires a session token generated at app startup.
- No outbound network calls at runtime except to `127.0.0.1` (Ollama, Postgres) and, during Web Agent tasks, the sites the user explicitly asked to visit.
- Content retrieved from the web or from WhatsApp is treated as untrusted data and is explicitly prevented from being interpreted as agent instructions (prompt-injection defense, detailed in System Design).
- Secrets (session token, DB credentials) are never logged; the WebSocket token is not passed as a query parameter to any third-party endpoint.

### 6.4 Data integrity

- Personal-notes, sample-dialogue, and scripture content are stored as logically separated collections and must never be retrieved into the wrong context (see PRD FR-4.2, System Design §Memory Layer).
- All destructive or externally-visible tool calls are logged to the episodic memory table with arguments, regardless of whether they were approved or denied.

## 7. Compatibility requirements

- Tauri build requires MSVC Build Tools and the WebView2 Runtime (pre-installed on Windows 11; must be installed manually on Windows 10).
- `code` (VS Code) must be on PATH — enabled via the "Add to PATH" option during VS Code installation.
- WhatsApp Desktop must be installed for the `whatsapp://` protocol handler route to work.
- `mpv` must be installed and reachable on PATH for local music playback.

## 8. Testing requirements

| Layer | Method | Pass criterion |
| --- | --- | --- |
| Event contract | JSON-schema validation of every published WebSocket event in dev builds | Zero schema violations |
| Graph routing | Fixed set of 30 sample utterances, asserted route | ≥ 95% correct routing, re-run on every prompt change |
| VRAM discipline | Automated script running 3 sequential tasks, checking `ollama ps` between each | GPU model list empty between tasks, no partial CPU offload |
| Browser tools | Selector tests against a pinned local static-site fixture | Pass on every CI run; live-site runs are manual only |
| End-to-end | Weekly manual run of the 6 representative user stories from the PRD | All 6 complete or fail with a clear, surfaced reason |

## 9. Upgrade path (documented, not required for v1)

| VRAM tier | Change |
| --- | --- |
| 8 GB | Move router to GPU (`keep_alive=-1`), raise worker to 7B class |
| 12 GB+ | Restore three specialized worker models instead of one shared worker; add a persistently resident vision model (`keep_alive="10m"`) |

---
*See companion documents: Product Requirements, Architecture, System Design, Plan, and the Phase-Wise Plan.*
