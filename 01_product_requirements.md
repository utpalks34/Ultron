# Ultron — Product Requirements Document (PRD)

**Product:** Ultron — Offline Multi-Agent Desktop Assistant
**Owner:** Single-user personal deployment
**Target machine:** Windows, GTX 1650 (4 GB VRAM, GDDR6), 16 GB RAM
**Status:** Draft v1

---

## 1. Vision

Ultron is a fully offline desktop assistant that lives in a 3D holographic HUD and does real work on the user's own machine: launching and closing applications, playing music, drafting WhatsApp messages, browsing the web to complete tasks, debugging code in VS Code, and answering questions from the user's own notes and a personal text corpus (including scripture excerpts) — all without a network dependency and without any data leaving the device.

The product succeeds if the user can say or type a command and have it either **done** or **clearly declined with a reason**, every time, with no silent failures.

## 2. Problem statement

Cloud assistants can't act on a local desktop, can't guarantee privacy for personal notes and scripture study, and add latency and a network dependency for things that should be instant and local. Existing local-LLM setups are chat-only — they answer questions but don't drive the OS, the browser, or media playback, and they give no visual sense of what the system is doing or how much of the machine's limited resources it is using.

## 3. Goals

1. Execute a bounded set of everyday commands locally and reliably: open/close apps, play music, compose WhatsApp messages, search the web, debug terminal errors.
2. Answer questions from a personal knowledge base (notes, sample dialogues, Gita verses) without the categories bleeding into each other.
3. Run entirely offline, on a 4 GB GPU laptop/desktop, without becoming unusable under that constraint.
4. Present system state — listening, thinking, acting, which agent is active, VRAM load — through a 3D HUD that makes an otherwise invisible pipeline legible at a glance.
5. Never take an irreversible or externally visible action (sending a message, deleting a file, closing an app) without a human confirmation step.

### Non-goals

- Multi-user support, cloud sync, or mobile companion apps.
- General-purpose chat quality competitive with cloud frontier models — the router and worker are small models chosen to fit 4 GB of VRAM, not to maximize benchmark scores.
- Fully autonomous unattended operation with no human in the loop for destructive actions.
- Reading WhatsApp messages and auto-replying without approval (drafting is in scope; unattended sending is not).

## 4. Target user

One person, technically capable, running Windows on a single machine with a GTX 1650 and 16 GB RAM. Comfortable with a terminal and willing to maintain a small local registry of their own apps, contacts, and music library rather than expecting the system to discover these automatically.

## 5. Functional requirements

### 5.1 The 3D holographic HUD

- **FR-1.1** A central 3D mesh (orb) that visibly reacts in real time to microphone input while listening and to synthesized speech while speaking.
- **FR-1.2** A 3D node graph showing which agent — Supervisor, Dev, Web, OS, RAG — is currently active, with a visible transition as control passes between agents.
- **FR-1.3** A glassmorphism overlay showing: current VRAM usage against total, streaming chat history, and a console of the most recent errors with enough detail (message, source, traceback) to debug from the UI alone.
- **FR-1.4** Visible, non-blocking indication whenever a heavier model is loading, so the user understands a pause rather than perceiving it as a freeze.

### 5.2 Orchestration and connectivity

- **FR-2.1** A persistent, auto-reconnecting local WebSocket connection between the UI and the backend, authenticated by a session token, bound to loopback only.
- **FR-2.2** The UI reflects backend agent state changes within one visible frame of the event being published — no polling.
- **FR-2.3** The user can cancel an in-progress task at any time (voice/gesture/UI control) and the system returns to idle within a bounded time.

### 5.3 Multi-agent task execution

- **FR-3.1** A supervisor classifies each request and routes it to exactly one of: Dev Agent, Web/UI Agent, OS Agent, RAG Agent, or finishes the turn.
- **FR-3.2 Web/UI Agent:** complete browser tasks such as "search for [product] on [site]" and return a structured summary (e.g., top results with prices); open native protocol handlers such as WhatsApp with a pre-filled message.
- **FR-3.3 OS Agent:** open a named application, close a named application (graceful, then forced only on confirmation), close all non-essential applications on command, play/pause/skip music from a local library, control volume, resolve spoken/typed names against a local registry rather than guessing.
- **FR-3.4 Dev Agent:** open VS Code (optionally to a given folder), capture a screenshot of the terminal panel, and provide a diagnosis of the visible error.
- **FR-3.5 RAG Agent:** answer questions from the user's personal notes with cited source chunks; answer questions about Gita verses only when the query is about scripture; never blend the two; run a corrective retrieval loop (retrieve → grade → rewrite → retry) before admitting it has no answer, rather than fabricating one.
- **FR-3.6** If no tool or registry entry matches a spoken/typed command, the system says so explicitly rather than attempting an unsupported action.

### 5.4 Personal knowledge base

- **FR-4.1** The user can ingest personal notes, a set of sample dialogues (style examples), and Gita verses as three logically separate collections.
- **FR-4.2** Sample dialogues influence response style only — they must never be presented as retrieved facts or cited as a source.
- **FR-4.3** Verse-level lookup (e.g., "what does chapter 2 verse 47 say") returns the exact verse, not a similarity guess.

### 5.5 Voice and sensory input

- **FR-5.1** Push-to-talk voice input transcribed locally, with visible interim/final transcript in the HUD.
- **FR-5.2** Local text-to-speech for responses, with the orb visibly reacting to output amplitude while speaking.
- **FR-5.3** (Optional, later phase) A small, deliberately limited gesture vocabulary — mute toggle, cancel, dismiss — each debounced so incidental movement doesn't trigger an action.

### 5.6 Safety and control

- **FR-6.1** Every tool is classified by risk (read / navigate / write-local / send / destructive). Send and destructive actions require an explicit user confirmation delivered through the UI before execution.
- **FR-6.2** If a confirmation request times out, the action is treated as denied, never as approved.
- **FR-6.3** A small, hard-coded set of processes (window manager, Ollama, PostgreSQL, the backend itself) can never be closed by a "close everything" command.
- **FR-6.4** A manual kill switch (hotkey, tray icon, and a physical mouse-corner failsafe) stops any in-progress automation immediately.
- **FR-6.5** Content read from external sources (web pages, WhatsApp messages) is treated as data, never as instructions to the agent, even if it contains imperative-looking text.

## 6. Non-functional requirements (summary — full detail in Technical Requirements)

- 100% offline at runtime: no cloud API calls, no telemetry, no remote fonts or scripts.
- Must run within 4 GB dedicated VRAM and 16 GB system RAM without swapping to disk under normal use.
- Perceived responsiveness: a cold model load should be visibly communicated within 300 ms of being triggered; a voice utterance should begin transcription feedback within roughly 1 second of speech ending.
- All persistent data (notes, chat history, episodic memory, registries) stored locally in PostgreSQL; no data leaves the machine.

## 7. Representative user stories

- *As the user*, I say "open VS Code and check my terminal error," and Ultron opens the editor, screenshots the terminal, and tells me what's wrong — without me touching the keyboard.
- *As the user*, I say "search Flipkart for the Samsung S26," and I see a top-three result summary in the chat log within about 15–20 seconds, including price.
- *As the user*, I say "message Arjun on WhatsApp that I'm running late," and WhatsApp opens with the message pre-filled in his chat; I press Enter myself.
- *As the user*, I ask "what does chapter 2 verse 47 say," and I get the exact verse, not a paraphrase pulled from an unrelated source.
- *As the user*, I say "close everything," and I see a list of what's about to close and confirm before anything happens.
- *As the user*, I ask a question my notes don't cover, and Ultron tells me plainly that it doesn't have that information rather than guessing.

## 8. Success criteria

| Criterion | Target |
| --- | --- |
| Command success rate (registry-backed: apps, music, WhatsApp compose) | ≥ 95% on a fixed 20-command test set |
| Web task success rate (search-and-summarize class tasks) | ≥ 70% (browser automation is inherently less reliable; see Technical Requirements) |
| RAG answer correctness on personal notes | ≥ 90% on a held-out question set, with correct source attribution |
| Zero cross-contamination between scripture and personal-notes retrieval | 100% on a fixed test set of 20 mixed queries |
| No unconfirmed destructive/send action | 0 occurrences, ever |
| Peak dedicated VRAM during any task | < 2,900 MB |
| End-to-end voice round trip (speak → orb reacts → answer spoken) | Completes without manual keyboard intervention |

## 9. Constraints and assumptions

- Single GPU, 4 GB VRAM, Windows OS — see Technical Requirements for the resulting model-tier decisions.
- The user will maintain a small local registry (apps, contacts, music) rather than the system auto-discovering everything; this trade directly enables FR-3.3's reliability target.
- Browser automation quality is bounded by what a small (3B-class) local model can reliably reason over; complex multi-step web tasks are best-effort, not guaranteed.

## 10. Open items

- Confirm final choice of worker model (`qwen2.5-coder:3b` vs `llama3.2:3b`) against real command set — see Plan.
- Confirm initial personal-notes ingestion format and volume, which determines chunking strategy.
- Decide whether WhatsApp read/auto-draft (vs. compose-only) is in scope for a later phase.

---
*See companion documents: Technical Requirements, Architecture, System Design, Plan, and the Phase-Wise Plan.*
