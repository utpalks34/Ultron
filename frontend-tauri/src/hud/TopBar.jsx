import { useUltron } from "../store/ultronStore";
import VramGauge from "./VramGauge";

const ICON_BTN =
  "pointer-events-auto grid h-8 w-8 place-items-center rounded-lg border transition-colors motion-reduce:transition-none";

function PanelIcon({ side }) {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="3" />
      <path d={side === "left" ? "M9 4v16" : "M15 4v16"} />
    </svg>
  );
}

// `panel` is "left" | "right" | null: which rail is open as an overlay. Below 1180 px the
// rails are hidden by default and these two buttons open them; above it they are always shown.
export default function TopBar({ panel, setPanel }) {
  const connected = useUltron((s) => s.connected);
  const devMode = useUltron((s) => s.devMode);
  const toggleDevMode = useUltron((s) => s.toggleDevMode);

  const toggle = (side) => setPanel(panel === side ? null : side);

  return (
    <header
      data-tauri-drag-region
      className="pointer-events-auto flex h-12 shrink-0 items-center justify-between px-4"
    >
      <div className="pointer-events-none flex select-none items-center gap-2.5">
        <span
          className="h-2.5 w-2.5 rounded-full bg-accent"
          style={{ boxShadow: "0 0 14px rgb(var(--accent-rgb) / 0.9)" }}
        />
        <span className="font-display text-[15px] font-semibold tracking-tight">Ultron</span>
      </div>

      <div className="flex items-center gap-2.5">
        <button
          type="button"
          onClick={() => toggle("left")}
          aria-label="Quick actions"
          aria-pressed={panel === "left"}
          className={`${ICON_BTN} min-[1180px]:hidden ${
            panel === "left" ? "border-accent/50 text-accent" : "border-line/50 text-muted hover:text-fg"
          }`}
        >
          <PanelIcon side="left" />
        </button>
        <button
          type="button"
          onClick={() => toggle("right")}
          aria-label="System status"
          aria-pressed={panel === "right"}
          className={`${ICON_BTN} min-[1180px]:hidden ${
            panel === "right" ? "border-accent/50 text-accent" : "border-line/50 text-muted hover:text-fg"
          }`}
        >
          <PanelIcon side="right" />
        </button>

        <VramGauge compact />

        <span
          className={`pointer-events-none flex items-center gap-2 rounded-full border px-3 py-1 text-xs ${
            connected
              ? "border-line/60 text-muted"
              : "border-red-400/40 bg-red-500/10 text-red-200"
          }`}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-emerald-400" : "bg-red-400"}`} />
          {connected ? "Online" : "Reconnecting to backend"}
        </span>

        <button
          type="button"
          onClick={toggleDevMode}
          aria-label="Toggle developer panel"
          aria-pressed={devMode}
          className={`${ICON_BTN} ${
            devMode
              ? "border-accent/50 text-accent"
              : "border-transparent text-muted/60 hover:text-fg"
          }`}
        >
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
            <circle cx="12" cy="12" r="3" />
            <path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M18.7 5.3l-2.1 2.1M7.4 16.6l-2.1 2.1" />
          </svg>
        </button>
      </div>
    </header>
  );
}
