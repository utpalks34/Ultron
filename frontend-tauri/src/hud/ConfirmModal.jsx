import { useEffect, useRef, useState } from "react";
import { useUltron } from "../store/ultronStore";

const BADGE = {
  destructive: "text-red-300",
  send: "text-amber-300",
};
const APPROVE = {
  destructive: "border-red-400/50 bg-red-500/20 text-red-100 hover:bg-red-500/30",
  send: "border-amber-300/50 bg-amber-400/20 text-amber-100 hover:bg-amber-400/30",
};
const APPROVE_DEFAULT = "border-accent/50 bg-accent/20 text-fg hover:bg-accent/30";
const BAR = {
  destructive: "bg-red-400",
  send: "bg-amber-300",
};
// what each risk means, in words the person deciding will understand
const RISK_TITLE = {
  destructive: "This can't be undone",
  send: "This leaves your computer",
};

export default function ConfirmModal({ send }) {
  const first = useUltron((s) => s.pendingConfirms[0]);
  const queued = useUltron((s) => s.pendingConfirms.length);
  const resolveConfirm = useUltron((s) => s.resolveConfirm);
  const [now, setNow] = useState(() => Date.now());

  const id = first?.action_id;
  const totalMs = first ? first.timeoutS * 1000 : 0;
  const remainingMs = first ? Math.max(0, totalMs - (now - first.receivedAt)) : 0;
  const expired = Boolean(first) && remainingMs <= 0;

  useEffect(() => {
    if (!id) return undefined;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [id]);

  // the backend has already denied a timed-out request; just drop it locally
  useEffect(() => {
    if (expired) resolveConfirm(id);
  }, [expired, id, resolveConfirm]);

  const respond = (approved) => {
    if (!first) return;
    if (send("confirm_response", { action_id: first.action_id, approved })) {
      resolveConfirm(first.action_id);
    }
  };
  const respondRef = useRef(respond);
  respondRef.current = respond;

  useEffect(() => {
    if (!id) return undefined;
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      respondRef.current(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [id]);

  if (!first) return null;

  const risk = first.risk;
  const pct = totalMs > 0 ? Math.min(100, (remainingMs / totalMs) * 100) : 0;

  return (
    <div
      key={first.action_id}
      role="alertdialog"
      aria-modal="true"
      className="pointer-events-auto fixed inset-0 z-50 flex items-center justify-center bg-ink/70 backdrop-blur-sm"
    >
      <div className="card-soft w-[26rem] max-w-[calc(100vw-2rem)] p-5">
        <div className={`text-[15px] font-semibold ${BADGE[risk] ?? "text-accent"}`}>
          {RISK_TITLE[risk] ?? "Ultron needs your approval"}
        </div>

        <div className="mt-3 max-h-48 overflow-y-auto whitespace-pre-wrap break-words font-mono text-[13px] text-fg/90">
          {first.description}
        </div>

        <div className="mt-4">
          <div className="h-1 w-full overflow-hidden rounded-full bg-line/50">
            <div
              className={`h-full ${BAR[risk] ?? "bg-accent"}`}
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="mt-1.5 text-xs text-muted">
            Denied automatically in {Math.ceil(remainingMs / 1000)} s
          </div>
        </div>

        <div className="mt-4 flex gap-2">
          <button
            type="button"
            autoFocus
            onClick={(e) => {
              // a rapid double-click must not also answer the next queued request
              if (e.detail !== 1) return;
              respond(false);
            }}
            className="flex-1 rounded-xl border border-line bg-raised/60 px-3 py-2.5 text-[13px] font-medium transition-colors hover:bg-raised motion-reduce:transition-none"
          >
            Deny
          </button>
          <button
            type="button"
            // only a pointer click approves; a keyboard-triggered click (Enter / Space) has detail 0
            onClick={(e) => {
              // detail 0 = keyboard, detail > 1 = rapid double-click; only a genuine single click approves
              if (e.detail !== 1) return;
              respond(true);
            }}
            className={`flex-1 rounded-xl border px-3 py-2.5 text-[13px] font-medium transition-colors motion-reduce:transition-none ${
              APPROVE[risk] ?? APPROVE_DEFAULT
            }`}
          >
            Approve
          </button>
        </div>

        {queued > 1 && (
          <div className="mt-3 text-xs text-muted">
            {queued - 1} more waiting
          </div>
        )}
      </div>
    </div>
  );
}
