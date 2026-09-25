import { useEffect, useRef, useState } from "react";
import { useUltron } from "../store/ultronStore";

const SHOW_MAX = 3;
const LIFETIME_MS = 6000;
const FADE_MS = 300;

// Full strings so Tailwind can see them.
const TONE = {
  fatal: "border-l-red-400 text-red-200",
  error: "border-l-red-400 text-red-200",
  warn: "border-l-amber-300 text-amber-200",
  info: "border-l-accent text-fg",
};

// Error payloads carry no id, so each object gets one the first time it is seen.
const ids = new WeakMap();
let nextId = 1;
const idOf = (err) => {
  let id = ids.get(err);
  if (id === undefined) {
    id = nextId++;
    ids.set(err, id);
  }
  return id;
};

function Toast({ err, onDone }) {
  const [shown, setShown] = useState(false);
  // the parent passes a fresh closure each render; the timers must not restart for it
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => {
    const enter = setTimeout(() => setShown(true), 16);
    const leave = setTimeout(() => setShown(false), LIFETIME_MS);
    const done = setTimeout(() => doneRef.current(), LIFETIME_MS + FADE_MS);
    return () => {
      clearTimeout(enter);
      clearTimeout(leave);
      clearTimeout(done);
    };
  }, []);

  return (
    <div
      role="status"
      className={`pointer-events-auto card-soft rounded-2xl border-l-2 px-4 py-2.5 text-[13px] transition-all duration-300 motion-reduce:transition-none ${
        TONE[err.severity] ?? TONE.info
      } ${shown ? "translate-y-0 opacity-100" : "-translate-y-2 opacity-0"}`}
    >
      <div className="line-clamp-2 break-words">
        <span className="font-medium">{err.source}</span>
        <span className="text-muted">: </span>
        {err.message}
      </div>
    </div>
  );
}

export default function Toasts() {
  const errors = useUltron((s) => s.errors);
  const [dismissed, setDismissed] = useState(() => new Set());

  // only error / fatal interrupt; info and warn stay in the developer panel's error list
  const visible = errors
    .filter((e) => e.severity === "error" || e.severity === "fatal")
    .slice(-SHOW_MAX)
    .filter((e) => !dismissed.has(idOf(e)))
    .reverse();

  return (
    <div className="pointer-events-none fixed left-1/2 top-14 z-40 w-[26rem] max-w-[calc(100vw-2rem)] -translate-x-1/2 space-y-2">
      {visible.map((e) => {
        const id = idOf(e);
        return (
          <Toast
            key={id}
            err={e}
            onDone={() => setDismissed((prev) => new Set(prev).add(id))}
          />
        );
      })}
    </div>
  );
}
