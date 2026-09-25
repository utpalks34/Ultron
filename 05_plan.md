# Ultron — Plan

High-level build strategy, hardware framing, testing approach, risks, and de-scoping guidance. The detailed phase-by-phase schedule is in the companion Phase-Wise Plan document.

---

## 1. Build strategy

Build the wire before the intelligence. The WebSocket event bus and the model manager exist before any real agent logic, because every later phase is testable in isolation only once those two things work. Concretely: get a hardcoded event flowing from backend to a rendering orb first (Phase 1–2), then a stubbed graph animating the node display (Phase 3), then real inference under strict VRAM discipline (Phase 4) — which is the viability gate for this hardware — and only after that, real tool integrations (Phases 5–8).

Phase 4 is called out specifically because it is the phase that decides whether the project works at all on a 4 GB card. If the worker model cannot hold the GPU alone at a usable token rate, nothing built afterward compensates for it. Everything before Phase 4 is deliberately de-risked so that the hardware question gets answered early, not after weeks of feature work.

## 2. Hardware framing

Target machine: GTX 1650 (4 GB VRAM, GDDR6), 16 GB RAM, Windows. Full budget tables are in the Technical Requirements document. The headline decisions that framing produces:

- The router and embedder run on CPU, permanently resident — this is what leaves the GPU free for a 3B worker instead of forcing everything down to 1.5B.
- One worker model, not three — specialization is traded for zero swap-time between consecutive agent hops, which matters more on this hardware than model specialization does.
- 7B-class models are excluded from the interactive loop entirely; they run at 4–6 tokens/second here, which is unusable across a multi-tool-call turn.

## 3. Testing strategy

| Layer | Approach | Cadence |
| --- | --- | --- |
| Event contract | JSON-schema validate every published event in dev | Every run |
| Graph routing | 30 fixed utterances → asserted route | Every prompt edit |
| VRAM discipline | Scripted 3-task run asserting `ollama ps` returns to CPU-only between tasks | Every change to `ollama_manager.py` or agent prompts |
| Browser tools | Selector tests against a pinned local fixture | CI; live-site runs manual only |
| Frontend | React Testing Library for HUD components; store→uniform mapping tested in isolation for R3F | Every PR |
| End to end | Weekly manual run of the 6 representative tasks from the PRD | Weekly |

## 4. Risks and mitigations

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Small router mis-routes multi-step tasks | High | Few-shot the routing prompt with real utterances; log every route to `episodes` and review weekly |
| Model swap latency feels sluggish | High | Speculative warm on the router's first route token; collapse to one worker model (already the plan) |
| Playwright selectors break on site redesigns | High | Accessibility-tree targeting over CSS selectors; treat the web agent as best-effort |
| Worker produces malformed tool-call JSON | Medium | Structured output via pydantic, one retry with the validation error appended, then fail visibly |
| Shader/bloom tanks framerate on this GPU | Medium | Capped device pixel ratio, bloom toggle, fallback flat-shaded orb |
| `pyautogui` acts on the wrong window | Medium | Foreground window verified before every click; confirmation gate on destructive actions |
| Whisper + worker + embedder exceed VRAM together | Medium | STT runs on CPU by design; not a GPU consumer at all |
| Tauri sidecar orphaned on crash | Low | PID file plus a startup sweep that kills stale backend processes |
| Corpus cross-contamination (scripture answered as personal fact, or vice versa) | Medium | Collection-scoped retrieval, enforced at the query level, not the prompt level |
| Unattended destructive action | Low, high severity | Confirmation gate with deny-on-timeout; hard-coded protected-process list |

## 5. De-scoping order

If the timeline compresses, cut in this order — first cut first:

1. **Gestures** — highest effort-to-value ratio in the plan; the orb and HUD already carry most of the perceived intelligence.
2. **Wake word** — push-to-talk is nearly as good and considerably more reliable to build.
3. **The 3D node graph** — keep the orb; render agent state as simple HUD chips instead. The orb alone carries the bulk of the "it feels alive" effect.
4. **Dev agent's vision loop** — text-only error paste captures most of the value without the screenshot-and-vision-model pipeline.

**Do not cut**, regardless of time pressure: the event bus, the VRAM manager, or the confirmation gate. Each is close to impossible to retrofit cleanly, and the first two are what the entire design rests on.

## 6. Decisions still open

| Question | Status | Notes |
| --- | --- | --- |
| Target GPU | **Resolved** | GTX 1650, 4 GB GDDR6 |
| OS | **Resolved** | Windows |
| Worker model: `qwen2.5-coder:3b` vs `llama3.2:3b` | Open | Test both against 5+ real personal commands before committing; coder-tuned models sometimes under-perform on general instruction-following |
| Personal-notes ingestion format and initial volume | Open | Drives chunking strategy; decide before Phase 5 |
| Which 5 tasks matter most day-to-day | Partially resolved | Flipkart-style search, WhatsApp compose, VS Code debugging are named; confirm the rest before finalizing Phase 6's tool list |
| WhatsApp read/auto-draft scope | Deferred | Compose-only via protocol handler is the v1 default; Web-based reading is a later-phase option, not required for launch |

## 7. Success signal

The project is on track if, by the end of Phase 4, `check_vram.py` passes cleanly and the worker sustains 30+ tokens/second. That single checkpoint is the leading indicator for whether the rest of the plan is achievable on this hardware as specified.

---
*See companion documents: Product Requirements, Technical Requirements, Architecture, System Design, and the Phase-Wise Plan.*
