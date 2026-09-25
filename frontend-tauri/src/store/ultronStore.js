import { create } from "zustand";

const MAX_MSGS = 200;

export const useUltron = create((set) => ({
  phase: "idle",              // idle | listening | thinking | acting | speaking | error
  activeNode: null,
  activeModel: null,
  messages: [],               // {role, text, done}
  errors: [],
  vram: { used_mb: 0, total_mb: 0, active_model: null },
  connected: false,
  stateEvents: 0,             // heartbeat counter, visible in the UI for Phase 1 testing
  previewPhase: null,         // dev-only local override for testing the orb
  micEnabled: false,          // local visual mic test (drives the orb only, not STT)
  micError: null,
  nodes: {},                  // name -> {status, ts, ms}
  runId: null,
  activity: [],               // last 20 tool_call events
  modelPhase: null,           // {phase, model} of the latest model event
  vramHistory: [],            // last 120 used_mb samples for the sparkline
  pendingConfirms: [],        // {action_id, description, risk, timeoutS, receivedAt}
  screenshot: null,           // {png, caption, ts} of the latest screenshot event
  transcript: null,           // {text, final} from the last "transcript" event
  devMode: false,             // shows DevPanel (debug tools); off by default
  historyOpen: false,         // right-edge conversation drawer; closed by default

  toggleDevMode: () => set((s) => ({ devMode: !s.devMode })),
  toggleHistory: () => set((s) => ({ historyOpen: !s.historyOpen })),
  resolveConfirm: (id) =>
    set((s) => ({ pendingConfirms: s.pendingConfirms.filter((c) => c.action_id !== id) })),
  clearScreenshot: () => set({ screenshot: null }),

  setPreviewPhase: (p) => set({ previewPhase: p }),
  setMicEnabled: (v) => set(v ? { micEnabled: true, micError: null } : { micEnabled: false }),
  setMicError: (e) => set({ micError: e }),

  addUserMessage: (text) =>
    set((s) => ({
      messages: [...s.messages, { role: "user", text, done: true }].slice(-MAX_MSGS),
    })),

  apply: (evt) =>
    set((s) => {
      const p = evt.payload ?? {};
      switch (evt.type) {
        case "state":
          return {
            phase: p.phase,
            stateEvents: s.stateEvents + 1,
            runId: p.phase === "idle" ? null : (evt.run_id ?? s.runId),
            // the backend denies every pending request when a run ends
            ...(p.phase === "idle" ? { pendingConfirms: [] } : {}),
            // an old transcript must not linger into the next listen
            ...(p.phase === "listening" ? { transcript: null } : {}),
          };
        case "transcript":
          return { transcript: p };
        case "confirm_request":
          if (s.pendingConfirms.some((c) => c.action_id === p.action_id)) return {};
          return {
            pendingConfirms: [
              ...s.pendingConfirms,
              {
                action_id: p.action_id,
                description: p.description,
                risk: p.risk,
                timeoutS: p.timeout_s ?? 60,
                receivedAt: Date.now(),
              },
            ],
          };
        case "screenshot":
          return { screenshot: { png: p.png_b64, caption: p.caption, ts: Date.now() } };
        case "graph_node":
          return {
            activeNode: p.status === "active" ? p.node : (s.activeNode === p.node ? null : s.activeNode),
            nodes: { ...s.nodes, [p.node]: { status: p.status, ts: Date.now(), ms: p.ms ?? null } },
            runId: evt.run_id ?? s.runId,
          };
        case "tool_call":
          return {
            activity: [
              ...s.activity.slice(-19),
              { agent: p.agent ?? null, tool: p.tool, status: p.status, preview: p.args_preview ?? "", ts: Date.now() },
            ],
          };
        case "token": {
          const m = [...s.messages];
          const last = m[m.length - 1];
          if (last?.role === "assistant" && !last.done) {
            m[m.length - 1] = { ...last, text: last.text + p.text, done: p.done };
          } else if (p.text) {
            m.push({ role: "assistant", text: p.text, done: p.done });
          }
          return { messages: m.slice(-MAX_MSGS) };
        }
        case "vram":
          return { vram: p, vramHistory: [...s.vramHistory.slice(-119), p.used_mb] };
        case "model":
          return {
            activeModel: p.phase === "evicted" ? null : p.model,
            modelPhase: { phase: p.phase, model: p.model },
          };
        case "error":
          // error payloads carry no timestamp; `at` lets the HUD order and hide them
          return { errors: [...s.errors.slice(-49), { ...p, at: Date.now() }] };
        case "snapshot":
          return {
            phase: p.phase ?? s.phase,
            activeNode: p.active_node ?? null,
            activeModel: p.active_model ?? null,
            vram: { used_mb: 0, total_mb: 0, active_model: null, ...(p.vram ?? {}) },
            messages: (p.messages ?? []).map((m) => ({ ...m, done: true })),
            nodes: p.active_node ? { [p.active_node]: { status: "active", ts: Date.now(), ms: null } } : {},
            runId: null,
            modelPhase: null,
            pendingConfirms: [],  // the backend replays pending requests right after the snapshot
          };
        default:
          return {};
      }
    }),
}));
