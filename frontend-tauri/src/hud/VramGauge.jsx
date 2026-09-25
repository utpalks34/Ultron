import { useUltron } from "../store/ultronStore";

// Healthy shape of a run: flat baseline, sharp rise on load, plateau while the
// worker is resident, then a drop back to baseline when the run ends.
// `compact` is the top-bar chip (bar + percentage); the full card lives in RightRail and DevPanel.
export default function VramGauge({ compact = false }) {
  const vram = useUltron((s) => s.vram);
  const modelPhase = useUltron((s) => s.modelPhase);
  const history = useUltron((s) => s.vramHistory);

  const { used_mb, total_mb } = vram;
  const busy = modelPhase?.phase === "loading" || modelPhase?.phase === "unloading";
  const pct = total_mb > 0 ? Math.min(100, (used_mb / total_mb) * 100) : 0;
  const tone = pct > 88 ? "bg-red-400" : pct > 70 ? "bg-amber-300" : "bg-accent";

  if (compact) {
    return (
      <div
        role="img"
        aria-label={total_mb > 0 ? `VRAM ${used_mb} of ${total_mb} MB` : "VRAM unavailable"}
        className="pointer-events-none flex items-center gap-2 font-mono text-[11px] text-muted"
      >
        {total_mb === 0 ? (
          <span className="text-amber-300/80">vram n/a</span>
        ) : (
          <>
            <div className="h-1 w-16 overflow-hidden rounded-full bg-line/50">
              <div
                className={`h-full transition-[width] duration-500 ${tone} ${
                  busy ? "animate-pulse motion-reduce:animate-none" : ""
                }`}
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="w-8 text-right tabular-nums">{Math.round(pct)}%</span>
          </>
        )}
      </div>
    );
  }

  const points =
    history.length >= 2 && total_mb > 0
      ? history
          .map((v, i) => `${(i / (history.length - 1)) * 200},${28 - (v / total_mb) * 28}`)
          .join(" ")
      : null;

  return (
    <section className="card-flat p-4">
      <div className="flex items-baseline justify-between">
        <h2 className="label">GPU memory</h2>
        <div className="font-mono text-xs text-fg/90">
          {used_mb}
          <span className="text-muted">/{total_mb} MB</span>
        </div>
      </div>

      {total_mb === 0 ? (
        <div className="mt-2 text-xs text-amber-300/80">GPU readings unavailable</div>
      ) : (
        <div className="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-line/50">
          <div
            className={`h-full transition-[width] duration-500 ${tone} ${
              busy ? "animate-pulse motion-reduce:animate-none" : ""
            }`}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}

      <div
        className={`mt-2 truncate font-mono text-[11px] ${
          modelPhase?.phase === "loading" ? "text-amber-300" : "text-muted"
        }`}
      >
        {busy ? `${modelPhase.phase} ${modelPhase.model}...` : (vram.active_model ?? "GPU idle")}
      </div>

      {points && (
        <svg viewBox="0 0 200 28" className="mt-2 h-7 w-full text-accent" preserveAspectRatio="none" aria-hidden="true">
          <polyline fill="none" stroke="currentColor" strokeOpacity="0.75" strokeWidth="1.2" points={points} />
        </svg>
      )}
    </section>
  );
}
