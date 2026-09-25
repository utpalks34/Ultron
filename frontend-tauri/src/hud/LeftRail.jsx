import { useEffect, useRef, useState } from "react";
import { useUltron } from "../store/ultronStore";

const BARS = 36;

// Each prompt goes through the same path as typed text, so the router decides who handles it.
const ACTIONS = [
  {
    label: "Open browser",
    prompt: "open my browser",
    icon: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" />
      </>
    ),
  },
  {
    label: "Play music",
    prompt: "play my favourite music",
    icon: (
      <>
        <path d="M9 18V6l10-2v12" />
        <circle cx="7" cy="18" r="2" />
        <circle cx="17" cy="16" r="2" />
      </>
    ),
  },
  {
    label: "Pause music",
    prompt: "pause the music",
    icon: <path d="M9 5v14M15 5v14" />,
  },
  {
    label: "Volume up",
    prompt: "volume up",
    icon: (
      <>
        <path d="M4 9v6h4l5 4V5L8 9H4z" />
        <path d="M16.5 8.5a5 5 0 010 7" />
      </>
    ),
  },
  {
    label: "YouTube Music",
    prompt: "open youtube music",
    icon: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M10 8.5l5 3.5-5 3.5z" />
      </>
    ),
  },
  {
    label: "Check terminal",
    prompt: "why is my terminal erroring",
    icon: (
      <>
        <rect x="3" y="4" width="18" height="16" rx="3" />
        <path d="M7 10l3 2-3 2M12 15h5" />
      </>
    ),
  },
];

function greeting() {
  const h = new Date().getHours();
  if (h < 5) return "Still up";
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

// Bars breathe gently at rest and follow the mic level while listening or the TTS level
// while speaking, the same sources the orb uses. Heights are written straight to the DOM.
function Waveform() {
  const bars = useRef([]);

  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0;
    let t = 0;
    let level = 0;

    const draw = (lvl) => {
      for (let i = 0; i < BARS; i++) {
        const el = bars.current[i];
        if (!el) continue;
        const wave = 0.5 + 0.5 * Math.sin(t * 2 + i * 0.6);
        const env = 0.35 + 0.65 * Math.sin((Math.PI * (i + 0.5)) / BARS);
        const h = 4 + 26 * (0.08 * wave + Math.min(1, lvl * (0.4 + 0.6 * wave)) * env);
        el.style.height = `${h.toFixed(1)}px`;
      }
    };

    if (reduce) {
      draw(0);
      return undefined;
    }

    const tick = () => {
      t += 0.05;
      const s = useUltron.getState();
      const phase = s.previewPhase ?? s.phase;
      const raw = phase === "speaking" ? (window.__ultronRms ?? 0) : (window.__ultronMicRms ?? 0);
      level += (Math.min(1, raw * 4) - level) * 0.25;
      draw(level);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div className="mt-4 flex h-8 items-center gap-[3px]" aria-hidden="true">
      {Array.from({ length: BARS }, (_, i) => (
        <span
          key={i}
          ref={(el) => (bars.current[i] = el)}
          className="w-[3px] rounded-full bg-accent/80"
          style={{ height: "4px" }}
        />
      ))}
    </div>
  );
}

function Row({ title, hint, onClick, disabled, right }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex w-full items-center gap-3 rounded-2xl px-3 py-2.5 text-left transition-colors hover:bg-raised/70 disabled:opacity-40 disabled:hover:bg-transparent motion-reduce:transition-none"
    >
      <span className="min-w-0 flex-1">
        <span className="block text-[14px] font-medium">{title}</span>
        <span className="block truncate text-xs text-muted">{hint}</span>
      </span>
      {right}
    </button>
  );
}

export default function LeftRail({ submit, send, onToggleTalk }) {
  const runId = useUltron((s) => s.runId);
  const devMode = useUltron((s) => s.devMode);
  const toggleDevMode = useUltron((s) => s.toggleDevMode);

  const [hello, setHello] = useState(greeting);
  useEffect(() => {
    const t = setInterval(() => setHello(greeting()), 60_000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="flex h-full w-full flex-col gap-4 overflow-y-auto pb-1">
      <section className="card-soft p-5">
        <h1 className="font-display text-[26px] font-semibold leading-tight tracking-tight">{hello}</h1>
        <p className="mt-1 text-[15px] text-muted">What should we do?</p>
        <Waveform />
      </section>

      <section className="card-soft p-4">
        <h2 className="label px-1">Quick actions</h2>
        <div className="mt-3 grid grid-cols-3 gap-2">
          {ACTIONS.map((a) => (
            <button
              key={a.label}
              type="button"
              onClick={() => submit(a.prompt)}
              className="flex flex-col items-center gap-2 rounded-2xl border border-line/40 bg-ink/40 px-1 py-3 text-center text-xs leading-tight text-fg/90 transition-colors hover:border-accent/40 hover:bg-accent/10 motion-reduce:transition-none"
            >
              <span className="grid h-9 w-9 place-items-center rounded-xl bg-raised text-accent">
                <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  {a.icon}
                </svg>
              </span>
              {a.label}
            </button>
          ))}
        </div>
      </section>

      <section className="card-soft p-2">
        <h2 className="label px-3 pb-1 pt-2">Controls</h2>
        <Row
          title="Push to talk"
          hint="Works from any window"
          onClick={onToggleTalk}
          right={
            <span className="flex shrink-0 items-center gap-1">
              <kbd className="keycap">Ctrl</kbd>
              <kbd className="keycap">Shift</kbd>
              <kbd className="keycap">Space</kbd>
            </span>
          }
        />
        <Row
          title="Stop current task"
          hint={runId ? "Cancels the run in progress" : "Nothing is running"}
          disabled={!runId}
          onClick={() => runId && send("cancel", { run_id: runId })}
        />
        <Row
          title="Developer panel"
          hint="Agent graph, quick tests, orb preview"
          onClick={toggleDevMode}
          right={
            <span
              className={`h-2 w-2 shrink-0 rounded-full ${devMode ? "bg-accent" : "bg-line"}`}
              aria-label={devMode ? "On" : "Off"}
            />
          }
        />
      </section>
    </div>
  );
}
