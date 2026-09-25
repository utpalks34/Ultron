import { useEffect, useRef, useState } from "react";
import { useUltron } from "../store/ultronStore";
import TranscriptCaption from "./TranscriptCaption";

const USER_BUBBLE =
  "ml-auto rounded-2xl rounded-br-md border border-accent/30 bg-accent/15 text-fg";
const ASSISTANT_BUBBLE =
  "mr-auto rounded-2xl rounded-bl-md border border-line/50 bg-raised/70 text-fg/95";

// The centre column: an empty stage that the canvas orb shows through (its height is mirrored
// by stageHeight() in three/Scene.jsx), then the conversation thread and input in one card.
export default function Conversation({ submit, onToggleTalk }) {
  const messages = useUltron((s) => s.messages);
  const phase = useUltron((s) => s.phase);
  const listening = phase === "listening";

  const [draft, setDraft] = useState("");
  const listRef = useRef(null);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const onSubmit = (e) => {
    e.preventDefault();
    if (submit(draft)) setDraft("");
  };

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="relative shrink-0" style={{ height: "clamp(220px, 38vh, 380px)" }}>
        <TranscriptCaption />
      </div>

      <section
        aria-label="Conversation"
        className="card-stage pointer-events-auto mx-auto flex min-h-0 w-full max-w-[720px] flex-1 flex-col"
      >
        <div ref={listRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto p-5">
          {messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-1 text-center">
              <div className="text-[15px] font-medium">Nothing said yet</div>
              <div className="text-[13px] text-muted">Click the orb to talk, or type below.</div>
            </div>
          ) : (
            messages.map((m, i) => (
              <div
                key={i}
                className={`max-w-[85%] whitespace-pre-wrap break-words px-4 py-2.5 text-[14px] leading-relaxed ${
                  m.role === "user" ? USER_BUBBLE : ASSISTANT_BUBBLE
                }`}
              >
                {m.text}
                {m.role === "assistant" && !m.done && (
                  <span
                    aria-hidden="true"
                    className="ml-0.5 inline-block h-3.5 w-[2px] translate-y-0.5 animate-pulse bg-accent motion-reduce:animate-none"
                  />
                )}
              </div>
            ))
          )}
        </div>

        <form onSubmit={onSubmit} className="flex items-center gap-2 border-t border-line/40 p-3">
          <input
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-label="Message Ultron"
            placeholder="Type a message"
            className="min-w-0 flex-1 rounded-full border border-line/50 bg-ink/50 px-4 py-2.5 text-[14px] text-fg placeholder:text-muted/70 focus:border-accent/60 focus:outline-none"
          />
          <button
            type="button"
            onClick={onToggleTalk}
            aria-label={listening ? "Stop listening" : "Start listening"}
            aria-pressed={listening}
            className={`grid h-10 w-10 shrink-0 place-items-center rounded-full border transition-colors motion-reduce:transition-none ${
              listening
                ? "animate-pulse border-accent bg-accent text-ink motion-reduce:animate-none"
                : "border-line/60 text-muted hover:border-accent/50 hover:text-accent"
            }`}
          >
            <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true">
              <rect x="9" y="3" width="6" height="11" rx="3" />
              <path d="M5.5 11a6.5 6.5 0 0013 0M12 17.5V21" />
            </svg>
          </button>
          <button
            type="submit"
            disabled={!draft.trim()}
            aria-label="Send message"
            className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-accent text-ink transition-opacity disabled:opacity-30 motion-reduce:transition-none"
          >
            <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M5 12h14M13 6l6 6-6 6" />
            </svg>
          </button>
        </form>
      </section>
    </div>
  );
}
