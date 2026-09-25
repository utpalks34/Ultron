# Ultron — Phase-Wise Plan

Detailed, sequenced execution plan. Each phase has a concrete deliverable and a done-when test that runs in under a minute; do not start the next phase until the current one passes. Estimates assume one engineer familiar with the stack — double P2 and P3 if you're learning React Three Fiber and LangGraph simultaneously.

---

## P0 — Environment (~half a day)

Install Python 3.11, Node 20 LTS, Rust stable + MSVC Build Tools, PostgreSQL 16, pgAdmin 4, Ollama, `mpv`. Create the venv, `npm create tauri-app`, set the Ollama environment variables from the Technical Requirements document, and reboot so Ollama picks them up.

Do the registry work now, not at P5: run `schema.sql`, create `registry_apps`, `registry_media`, `registry_contacts`, and populate them by hand — ten apps, your contacts, a music scan. Everything in P6 resolves through these tables, and having them early means commands are testable the moment the graph runs.

**Done when:** `ollama run qwen2.5:1.5b-instruct` responds, `psql -c "CREATE EXTENSION vector;"` succeeds, `SELECT count(*) FROM registry_apps` returns non-zero, and `nvidia-smi` reports 4096 MiB total.

## P1 — The hollow bridge (~1 day)

FastAPI with one WebSocket endpoint that echoes a hardcoded `state` event every second. Tauri window with a React page that connects and logs envelopes to the console. No 3D, no LLM.

**Done when:** the Tauri window logs a backend heartbeat, and killing the backend triggers frontend reconnect with visible backoff.

## P2 — The orb (~2 days)

R3F canvas, icosahedron with a custom noise-displacement shader, `useUltronSocket` wired to a Zustand store, orb color driven by the `state` event. Add `useAudioLevel` (`getUserMedia` → `AnalyserNode`) and displace vertices by amplitude. Bloom via `@react-three/postprocessing`.

**Done when:** speaking into the mic visibly deforms the orb at 60 fps, and manually publishing `{"type":"state","payload":{"phase":"thinking"}}` from a Python REPL changes its color.

This is the phase to get right — every later feature renders through it, and shader debugging is much harder once agent state is also in motion.

## P3 — Supervisor skeleton (~2 days)

LangGraph `StateGraph` with a supervisor node and three stub workers returning canned strings. Stream `astream_events` into the bus. Build `AgentGraph.jsx`: nodes in a ring around the orb, emissive pulse on `graph_node` events.

**Done when:** typing "open my browser" lights the supervisor node, then the web node, then returns to idle — with no model doing real work yet.

## P4 — Real models and VRAM discipline (~2 days) — viability gate

Swap stubs for real inference under the split-device layout: router and embedder pinned to CPU, one 3B worker holding the GPU alone, evicted on return. Wire `telemetry()` to the VRAM gauge. Full step-by-step build below.

**Done when:** `ollama ps` shows the router at `100% CPU` permanently and the worker at `100% GPU` only during a task; dedicated VRAM never exceeds ~2.7 GB; the gauge returns to baseline within 2 s of the answer finishing.

If this phase does not pass cleanly on your hardware, stop and resolve it before proceeding — nothing later compensates for a broken model-swap loop on a 4 GB card.

### Phase 4 — detailed build

**Step 1 — Pull the models**

```powershell
ollama pull qwen2.5:1.5b-instruct    # router, CPU
ollama pull nomic-embed-text         # embedder, CPU
ollama pull qwen2.5-coder:3b         # worker, GPU
ollama pull moondream                # vision, GPU, solo (optional at P4)

ollama list   # confirm sizes: 1.0 / 0.27 / 1.9 / 1.7 GB
```

**Step 2 — `config.py`** — device placement as data, not scattered logic

```python
class Settings(BaseSettings):
    router_model: str = "qwen2.5:1.5b-instruct"
    worker_model: str = "qwen2.5-coder:3b"
    vision_model: str = "moondream"
    embed_model:  str = "nomic-embed-text"
    ctx_router: int = 2048
    ctx_worker: int = 4096
    vram_total_mb: int = 4096
    vram_reserve_mb: int = 700
    pg_dsn: str = "postgresql://ultron:ultron@127.0.0.1:5432/ultron"
    ollama_host: str = "http://127.0.0.1:11434"
    graph_nodes: set[str] = Field(default_factory=lambda: {
        "supervisor", "dev_agent", "web_agent", "os_agent", "rag_agent"})

settings = Settings()
CPU_MODELS = {settings.router_model, settings.embed_model}
GPU_MODELS = {settings.worker_model, settings.vision_model}
VRAM_COST_MB = {settings.worker_model: 2200, settings.vision_model: 1800}

def is_cpu(model: str) -> bool:
    return model in CPU_MODELS
```

**Step 3 — `llm/ollama_manager.py`** — enforces two rules: CPU models never touch the GPU, and the GPU has at most one tenant, held under a lock so two graph nodes can't race into it.

```python
class OllamaManager:
    def __init__(self, bus):
        self.bus = bus
        self._gpu_tenant: str | None = None
        self._gpu_lock = asyncio.Lock()

    async def _load(self, model: str, keep_alive):
        opts = {"num_ctx": settings.ctx_router if is_cpu(model) else settings.ctx_worker}
        if is_cpu(model):
            opts["num_gpu"] = 0
        await self._client.post("/api/generate",
            json={"model": model, "prompt": "", "keep_alive": keep_alive, "options": opts})

    async def _unload(self, model: str):
        await self._client.post("/api/generate",
            json={"model": model, "prompt": "", "keep_alive": 0})

    async def warm_resident(self):
        for model in CPU_MODELS:
            await self._load(model, keep_alive=-1)

    @asynccontextmanager
    async def worker(self, model: str):
        if model not in GPU_MODELS:
            raise ValueError(f"{model} is not a GPU model")
        async with self._gpu_lock:
            if self._gpu_tenant and self._gpu_tenant != model:
                await self._unload(self._gpu_tenant)
                self._gpu_tenant = None
                await asyncio.sleep(0.3)   # let the driver actually release
            if self._gpu_tenant != model:
                await self._load(model, keep_alive="5m")
                self._gpu_tenant = model
            yield model   # no unload here: the worker stays resident across the hops of one run

    async def release(self):
        """Evict the GPU tenant; called when a run ends."""
        async with self._gpu_lock:
            if self._gpu_tenant:
                await self._unload(self._gpu_tenant)
                self._gpu_tenant = None
```

The 300 ms sleep after an unload is not superstition — on Windows the driver releases memory asynchronously, and loading the next model immediately can see the old allocation still held, causing a spurious layer offload on a machine that actually had room.

**Step 4 — Router pinned to CPU**

```python
def _router_llm():
    return ChatOllama(model=settings.router_model, temperature=0,
                      num_gpu=0, num_ctx=settings.ctx_router,
                      keep_alive=-1).with_structured_output(Route)
```

**Step 5 — One worker, four roles** (see System Design §2 for the full factory pattern). Because all four agents share one resident model, consecutive hops between them cost zero swap time.

**Step 6 — VRAM gauge** — a Zustand-driven progress bar in the HUD (`VramGauge.jsx`) that colors amber above 70% and red above 88% of the 4096 MB budget, with the active model name printed below it.

**Step 7 — Verify** — a script that runs three tasks back-to-back and asserts `ollama ps` shows no GPU-resident worker between them and the CPU router stays at `100% CPU` throughout:

```python
TASKS = ["what is in my notes about the blueprint",
         "open my editor",
         "what does Gita 2.47 say"]
# run each via the same code path as a real user_utterance,
# assert "qwen2.5-coder" not in `ollama ps` after each, with a short settle delay
```

**Step 8 — Troubleshooting**

| Symptom | Cause | Fix |
| --- | --- | --- |
| Worker shows `100% CPU` in `ollama ps` | Free VRAM below model size at load time | Close Chromium, raise `OLLAMA_GPU_OVERHEAD`, check nothing else holds VRAM |
| ~4 tok/s generation | Partial layer offload | Check `nvidia-smi` during generation, not after |
| Router also on GPU | `num_gpu=0` missing on one call path | Grep every `ChatOllama(` call |
| VRAM never returns to baseline | An exception skipped cleanup | Confirm no agent calls Ollama outside `worker()` |
| First utterance after boot takes 8 s | `warm_resident()` not awaited before serving | Await it in lifespan startup, not as a background task |
| Gauge frozen at 0 | NVML import failed silently | Check startup warning; `pip install nvidia-ml-py` |
| Two workers loaded at once | A node called Ollama directly, bypassing the manager | All model access must go through `ollama.worker()` |

**Exit criteria:** `ollama ps` shows exactly the CPU router between tasks; peak dedicated VRAM stays under 2,900 MB with Chromium open; worker sustains 30+ tok/s; three consecutive tasks leave no resident GPU model; the verification script passes clean.

## P5 — Memory (~2 days)

`schema.sql` with the three-collection design, asyncpg pool, `ingest.py` over your notes / dialogue samples / Gita text (tagged into their respective collections), HNSW index, the RAG agent with retrieve → grade → rewrite → generate.

**Done when:** a question answerable only from your ingested notes returns a correct, cited answer; a scripture query returns the exact verse via metadata lookup; a question with no local support triggers a visible CRAG rewrite rather than a confident fabrication; and a mixed test set of 20 queries shows zero cross-contamination between `personal` and `scripture` collections.

## P6 — Hands (~3 days) — budget the most time here

Playwright with a persistent context so logins survive. `web_agent` with `goto`, `find_and_click`, `type_text`, `read_page`, `screenshot`. Then `desktop.py` with the confirmation gate, the registry-resolve command surface (apps, music via `mpv`, WhatsApp via protocol handler), and the protected-process "close everything" flow. Then `dev_agent`: launch VS Code, screenshot the terminal pane, feed to the vision model.

**Done when:** "search for Samsung S26 on Flipkart" completes end to end with a top-three summary in the chat log; "message [contact] that I'm running late" opens WhatsApp pre-filled; "play my favorite music" starts playback via `mpv`; "close everything" shows a confirmable list and respects the protected set; "why is my terminal erroring" produces a screenshot in the evidence panel plus a plausible diagnosis.

Browser automation is where plans meet reality — selectors drift, modals interrupt, pages load slower than any first-guess timeout. Expect most of this phase's time to go toward the web agent specifically.

## P7 — Voice loop (~2 days)

`faster-whisper` with Silero VAD in a worker thread, push-to-talk on a Tauri global hotkey first, wake word second (deferred per the de-scoping order in the Plan document). Piper TTS streaming to `sounddevice`, publishing `audio_level` per chunk, ducking music volume while speaking.

**Done when:** a full spoken round trip — speak, orb listens, agent acts, orb speaks while pulsing to Piper's output — runs without touching the keyboard.

## P8 — Polish and hardening (ongoing)

Gesture control (if not de-scoped), error console filtering, `tauri build` producing a signed installer, bundled model checksums, crash recovery from the LangGraph checkpointer.

**Done when:** a fresh machine runs `bootstrap.ps1`, then the installer, and reaches a working orb with no manual steps beyond `ollama pull`.

---

## Sequencing note

The order matters more than the day estimates: every phase after P1 is testable in isolation only because the event bus exists first, and P4 is deliberately placed before any real tool work so the hardware question — can a 4 GB card actually run this loop — gets answered in week one, not week four.

---
*See companion documents: Product Requirements, Technical Requirements, Architecture, System Design, and Plan.*
