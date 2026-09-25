import { useState } from "react";
import { useUltron } from "../store/ultronStore";
import VramGauge from "./VramGauge";

const PREVIEW_PHASES = ["idle", "listening", "thinking", "acting", "speaking", "error"];
const AGENT_ROWS = ["supervisor", "dev_agent", "web_agent", "os_agent", "rag_agent"];
const DOT_COLOR = {
  active: "bg-accent",
  done: "bg-emerald-400",
  failed: "bg-red-400",
  idle: "bg-line",
};
const QUICK_TESTS = [
  "open my browser",
  "open notepad",
  "close notepad",
  "close everything",
  "play my favourite music",
  "pause the music",
  "volume up",
  "search for Samsung S26 on Flipkart",
  "open youtube music",
  "why is my terminal erroring",
  "what is my gym locker number",
  "hello there",
  "!crash", // works only when the backend has DEBUG_ENDPOINTS=1
];

const CHIP =
  "rounded-lg border px-2 py-1 font-mono text-[11px] transition-colors hover:bg-raised motion-reduce:transition-none";
const CHIP_OFF = "border-line/50 bg-ink/40 text-fg/80";
const CHIP_ON = "border-accent/50 bg-accent/10 text-accent";

const SEVERITY_FILTERS = [
  { key: "all", label: "all", match: () => true },
  { key: "info", label: "info", match: (e) => e.severity === "info" },
  { key: "warn", label: "warn", match: (e) => e.severity === "warn" },
  { key: "error", label: "error", match: (e) => e.severity === "error" || e.severity === "fatal" },
];
// Full strings so Tailwind can see them.
const SEVERITY_DOT = {
  fatal: "bg-red-400",
  error: "bg-red-400",
  warn: "bg-amber-300",
  info: "bg-cyan-400",
};
const HEALTH_WINDOW = 20;

// "voice_ready" / "speech_ready" are not in the snapshot (the event schema has no field for
// them), so voice and database health are inferred from the last few errors. That is lower
// confidence than a real flag: an old error keeps a subsystem flagged until newer ones push it out.
function HealthRow({ errors }) {
  const connected = useUltron((s) => s.connected);
  const recent = errors.slice(-HEALTH_WINDOW);
  const voiceBad = recent.some((e) => e.source === "stt" || e.source === "tts");
  const dbBad = recent.some((e) => e.source === "database");
  return (
    <div>
      <div className="label">System health</div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px]">
        <span className="flex items-center gap-1.5">
          <span className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-400" : "bg-red-400"}`} />
          {connected ? "connected" : "disconnected"}
        </span>
        <span className="flex items-center gap-1.5">
          <span className={`h-2 w-2 rounded-full ${voiceBad ? "bg-amber-300" : "bg-emerald-400"}`} />
          {voiceBad ? "voice: check errors" : "voice: OK"}
        </span>
        <span className="flex items-center gap-1.5">
          <span className={`h-2 w-2 rounded-full ${dbBad ? "bg-amber-300" : "bg-emerald-400"}`} />
          {dbBad ? "database: check errors" : "database: OK"}
        </span>
      </div>
      <div className="mt-1 text-[10px] text-muted">
        voice and database are inferred from the last {HEALTH_WINDOW} errors, not reported directly.
      </div>
    </div>
  );
}

// The store's `errors` array is the authoritative history; "clear" only hides what is listed
// now, through a local timestamp, and the store is never mutated.
function ErrorConsole({ errors }) {
  const [filter, setFilter] = useState("all");
  const [clearedBeforeTs, setClearedBeforeTs] = useState(0);
  const active = SEVERITY_FILTERS.find((f) => f.key === filter) ?? SEVERITY_FILTERS[0];
  const rows = errors
    .filter((e) => (e.at ?? 0) > clearedBeforeTs && active.match(e))
    .slice()
    .reverse();

  return (
    <div>
      <div className="flex items-center justify-between">
        <div className="label flex items-center gap-2">
          errors
          <span className="rounded-full bg-raised px-1.5 font-mono text-[10px] text-fg/80">
            {errors.length}
          </span>
        </div>
        <button
          type="button"
          onClick={() => setClearedBeforeTs(Date.now())}
          className={`${CHIP} ${CHIP_OFF}`}
        >
          clear
        </button>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {SEVERITY_FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            onClick={() => setFilter(f.key)}
            className={`${CHIP} ${filter === f.key ? CHIP_ON : CHIP_OFF}`}
          >
            {f.label}
          </button>
        ))}
      </div>
      <div className="mt-2 max-h-64 space-y-2 overflow-y-auto">
        {rows.length === 0 ? (
          <div className="font-mono text-[11px] text-muted">-</div>
        ) : (
          rows.map((e, i) => (
            <div key={`${e.at}-${i}`} className="flex items-start gap-2">
              <span
                className={`mt-1 h-2 w-2 shrink-0 rounded-full ${SEVERITY_DOT[e.severity] ?? SEVERITY_DOT.info}`}
              />
              <div className="min-w-0 flex-1">
                <div className="font-mono text-[10px] text-cyan-200/50">{e.source}</div>
                <div className="whitespace-pre-wrap break-words font-mono text-[11px] text-cyan-100/80">
                  {e.message}
                </div>
                {e.traceback && (
                  <details className="mt-0.5">
                    <summary className="cursor-pointer font-mono text-[10px] text-muted">
                      traceback
                    </summary>
                    <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[10px] text-cyan-100/60">
                      {e.traceback}
                    </pre>
                  </details>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// Everything the old main view showed for debugging. Only mounted while devMode is on,
// so its store subscriptions cost nothing otherwise. It opens over the left rail.
export default function DevPanel({ send, submit, onToggleTalk }) {
  const devMode = useUltron((s) => s.devMode);
  if (!devMode) return null;
  return <DevPanelBody send={send} submit={submit} onToggleTalk={onToggleTalk} />;
}

function DevPanelBody({ send, submit, onToggleTalk }) {
  const toggleDevMode = useUltron((s) => s.toggleDevMode);
  const phase = useUltron((s) => s.phase);
  const stateEvents = useUltron((s) => s.stateEvents);
  const activeModel = useUltron((s) => s.activeModel);
  const previewPhase = useUltron((s) => s.previewPhase);
  const setPreviewPhase = useUltron((s) => s.setPreviewPhase);
  const micEnabled = useUltron((s) => s.micEnabled);
  const micError = useUltron((s) => s.micError);
  const setMicEnabled = useUltron((s) => s.setMicEnabled);
  const nodes = useUltron((s) => s.nodes);
  const runId = useUltron((s) => s.runId);
  const activity = useUltron((s) => s.activity);
  const errors = useUltron((s) => s.errors);

  return (
    <div className="card-soft pointer-events-auto fixed bottom-4 left-4 top-14 z-40 w-[300px] space-y-4 overflow-y-auto p-4">
      <div className="flex items-center justify-between">
        <h2 className="font-display text-[15px] font-semibold">Developer panel</h2>
        <button
          type="button"
          onClick={toggleDevMode}
          aria-label="Close developer panel"
          className="grid h-7 w-7 place-items-center rounded-lg text-muted transition-colors hover:bg-raised hover:text-fg motion-reduce:transition-none"
        >
          <span aria-hidden="true">×</span>
        </button>
      </div>

      <div className="space-y-0.5 font-mono text-xs text-fg/90">
        <div>{phase}</div>
        <div className="text-muted">state events: {stateEvents}</div>
        <div className="text-muted">{activeModel ?? "no model"}</div>
      </div>

      <VramGauge />

      <HealthRow errors={errors} />

      <div>
        <div className="label">Orb preview</div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {PREVIEW_PHASES.map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => setPreviewPhase(name)}
              className={`${CHIP} ${previewPhase === name ? CHIP_ON : CHIP_OFF}`}
            >
              {name}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setPreviewPhase(null)}
            className={`${CHIP} ${previewPhase === null ? CHIP_ON : CHIP_OFF}`}
          >
            live
          </button>
        </div>
      </div>

      <div>
        <div className="label">Mic visual test</div>
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setMicEnabled(!micEnabled)}
            className={`${CHIP} ${micEnabled ? CHIP_ON : CHIP_OFF}`}
          >
            {micEnabled ? "mic on" : "mic off"}
          </button>
          <div className="mt-1.5 text-[11px] text-muted">
            Deforms the orb only. It is not real speech input.
          </div>
          {micError && (
            <div className="mt-1.5 break-words font-mono text-[11px] text-red-300">{micError}</div>
          )}
        </div>
      </div>

      <div>
        <div className="label">Agent graph</div>
        <div className="mt-2 space-y-1">
          {AGENT_ROWS.map((name) => {
            const n = nodes[name];
            const status = n?.status ?? "idle";
            return (
              <div key={name} className="flex items-center gap-2 font-mono text-xs">
                <span className={`h-2 w-2 rounded-full ${DOT_COLOR[status] ?? DOT_COLOR.idle}`} />
                <span>{name}</span>
                <span className="text-muted">
                  {status}
                  {status === "done" && n?.ms != null ? ` · ${n.ms} ms` : ""}
                </span>
              </div>
            );
          })}
        </div>
        <div className="mt-2 truncate font-mono text-[11px] text-muted">run: {runId ?? "-"}</div>
        <div className="mt-2">
          <button
            type="button"
            disabled={!runId}
            onClick={() => runId && send("cancel", { run_id: runId })}
            className={`${CHIP} ${CHIP_OFF} disabled:opacity-40`}
          >
            cancel
          </button>
        </div>
      </div>

      <div>
        <div className="label">Activity</div>
        <div className="mt-2 max-h-24 space-y-0.5 overflow-y-auto font-mono text-[11px]">
          {activity.length === 0 ? (
            <div className="truncate text-muted">-</div>
          ) : (
            activity.slice(-6).map((a, i) => (
              <div key={i} className="truncate text-fg/80">
                {`${a.agent ?? "-"}/${a.tool} ${a.preview}`}
              </div>
            ))
          )}
        </div>
      </div>

      <ErrorConsole errors={errors} />

      <div>
        <div className="label">Quick tests</div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          <button type="button" onClick={onToggleTalk} className={`${CHIP} ${CHIP_OFF}`}>
            🎤 talk
          </button>
          {QUICK_TESTS.map((text) => (
            <button
              key={text}
              type="button"
              onClick={() => submit(text)}
              className={`${CHIP} ${CHIP_OFF}`}
            >
              {text}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
