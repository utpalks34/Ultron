import { useRef } from "react";
import { useUltron } from "../store/ultronStore";
import { AGENT_LABEL, PHASE_LABEL } from "./phase";

const GLOW = { textShadow: "0 0 24px rgb(var(--accent-rgb) / 0.35)" };

// One line under the orb. While listening it is what you are saying; otherwise it says
// what Ultron is doing. Replies live in the conversation thread, not here.
// Sits at the bottom of the stage in Conversation.jsx (the parent must be `relative`).
export default function TranscriptCaption() {
  const transcript = useUltron((s) => s.transcript);
  const phase = useUltron((s) => s.phase);
  const activeNode = useUltron((s) => s.activeNode);

  const live = Boolean(transcript?.text) && (phase === "listening" || !transcript.final);

  let text = "";
  if (live) {
    text = transcript.text;
  } else if (phase === "idle") {
    text = "Click the orb to talk";
  } else if ((phase === "thinking" || phase === "acting") && activeNode && AGENT_LABEL[activeNode]) {
    text = `${PHASE_LABEL[phase]} with ${AGENT_LABEL[activeNode]}`;
  } else {
    text = PHASE_LABEL[phase] ?? "";
  }

  // keep the last line while the next one is empty, so the row never collapses
  const kept = useRef("");
  if (text) kept.current = text;

  return (
    <div
      aria-live="polite"
      className="pointer-events-none absolute inset-x-0 bottom-0 flex h-10 items-center justify-center px-6 text-center"
    >
      <div
        className={
          live
            ? "line-clamp-1 text-lg font-light tracking-wide text-fg"
            : "text-[13px] text-muted"
        }
        style={live ? GLOW : undefined}
      >
        {kept.current}
      </div>
    </div>
  );
}
