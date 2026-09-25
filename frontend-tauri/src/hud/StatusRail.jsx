import { useUltron } from "../store/ultronStore";

// Tauri event emitted by src-tauri/src/lib.rs when Ctrl+Shift+Space is pressed.
export const PTT_EVENT = "ultron://ptt";

const DOT = {
  listening: "bg-green-400",
  speaking: "bg-green-400",
  thinking: "bg-amber-300",
};

export default function StatusRail() {
  const phase = useUltron((s) => s.phase);
  const errors = useUltron((s) => s.errors);
  const transcript = useUltron((s) => s.transcript);

  const last = errors[errors.length - 1];
  const voiceError = last && (last.source === "stt" || last.source === "tts") ? last : null;
  // set once by src-tauri/src/lib.rs at startup, so no subscription is needed
  const hotkeyError = typeof window !== "undefined" && window.__ULTRON_HOTKEY_ERROR__;

  return (
    <div className="glass p-3 flex flex-wrap items-center gap-3">
      <div className="flex items-center gap-2 font-mono text-[10px]">
        <span className={`h-2 w-2 rounded-full ${DOT[phase] ?? "bg-white/20"}`} />
        <span>{phase}</span>
      </div>
      <div className="font-mono text-[10px] text-cyan-200/50">Ctrl+Shift+Space to talk</div>
      {transcript?.final && transcript.text && (
        <div className="font-mono text-xs text-cyan-100/80 truncate max-w-full">
          {`"${transcript.text}"`}
        </div>
      )}
      {voiceError && (
        <div className="w-full text-[10px] text-red-300 truncate">
          {voiceError.source}: {voiceError.message}
        </div>
      )}
      {hotkeyError && (
        <div className="w-full text-[10px] text-red-300 truncate">
          hotkey unavailable: {hotkeyError}
        </div>
      )}
    </div>
  );
}
