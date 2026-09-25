import { useUltron } from "../store/ultronStore";

export default function ScreenshotPanel() {
  const screenshot = useUltron((s) => s.screenshot);
  const clearScreenshot = useUltron((s) => s.clearScreenshot);

  if (!screenshot) return null;

  return (
    <div className="card-soft pointer-events-auto absolute bottom-4 right-4 z-30 max-h-[60vh] w-[28rem] max-w-[calc(100vw-2rem)] overflow-auto p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="label truncate">{screenshot.caption}</div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="font-mono text-[11px] text-muted">
            {new Date(screenshot.ts).toLocaleTimeString()}
          </span>
          <button
            type="button"
            onClick={clearScreenshot}
            aria-label="Close screenshot"
            className="grid h-6 w-6 place-items-center rounded-md border border-line/50 text-muted transition-colors hover:text-fg motion-reduce:transition-none"
          >
            <span aria-hidden="true">×</span>
          </button>
        </div>
      </div>
      <img
        src={`data:image/png;base64,${screenshot.png}`}
        alt={screenshot.caption ?? "screenshot"}
        className="mt-2 w-full rounded-xl"
      />
    </div>
  );
}
