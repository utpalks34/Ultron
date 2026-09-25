import { useEffect, useRef, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { listen } from "@tauri-apps/api/event";
import Scene from "./three/Scene";
import { useUltron } from "./store/ultronStore";
import { useUltronSocket } from "./hooks/useUltronSocket";
import { useAudioLevel } from "./hooks/useAudioLevel";
import { PTT_EVENT } from "./hud/StatusRail";
import { PHASE_RGB } from "./hud/phase";
import TopBar from "./hud/TopBar";
import LeftRail from "./hud/LeftRail";
import Conversation from "./hud/Conversation";
import RightRail from "./hud/RightRail";
import Toasts from "./hud/Toasts";
import DevPanel from "./hud/DevPanel";
import ConfirmModal from "./hud/ConfirmModal";
import ScreenshotPanel from "./hud/ScreenshotPanel";

const HAS_TAURI = typeof window !== "undefined" && window.__TAURI_INTERNALS__ !== undefined;

// Rails are always visible from 1180 px up; below that they are overlays opened from the top bar.
const RAIL =
  "pointer-events-auto fixed top-14 bottom-4 z-30 w-[300px] min-h-0 min-[1180px]:static min-[1180px]:z-auto min-[1180px]:shrink-0";

export default function App() {
  const token = window.__TAURI_ULTRON_TOKEN__ ?? "dev";
  const { send } = useUltronSocket(token);
  useAudioLevel();

  const addUserMessage = useUltron((s) => s.addUserMessage);
  const [panel, setPanel] = useState(null); // "left" | "right" | null, overlays below 1180 px

  const submit = (raw) => {
    const text = raw.trim();
    if (!text) return false;
    if (!send("user_utterance", { text, source: "text" })) return false;
    addUserMessage(text);
    return true;
  };

  // push-to-talk: arm the backend mic unless it is already listening, then cancel
  const toggleTalk = () => {
    const enabled = useUltron.getState().phase !== "listening";
    send("mic", { enabled });
  };
  const toggleTalkRef = useRef(toggleTalk);
  toggleTalkRef.current = toggleTalk;

  // The whole interface takes the orb's colour: --accent-rgb follows the phase (or the
  // dev preview override, the same rule Orb.jsx uses).
  useEffect(() => {
    let last = "";
    const apply = (s) => {
      const rgb = PHASE_RGB[s.previewPhase ?? s.phase] ?? PHASE_RGB.idle;
      if (rgb === last) return;
      last = rgb;
      document.documentElement.style.setProperty("--accent-rgb", rgb);
    };
    apply(useUltron.getState());
    return useUltron.subscribe(apply);
  }, []);

  useEffect(() => {
    if (!HAS_TAURI) {
      console.info("push-to-talk hotkey needs the Tauri window; use the talk button in a browser");
      return undefined;
    }
    let unlisten = null;
    let disposed = false;
    listen(PTT_EVENT, () => toggleTalkRef.current())
      .then((fn) => {
        if (disposed) fn();
        else unlisten = fn;
      })
      .catch((e) => console.warn("could not listen for the push-to-talk hotkey", e));
    return () => {
      disposed = true;
      if (unlisten) unlisten();
    };
  }, []);

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-ink font-sans text-fg">
      <Canvas
        className="absolute inset-0"
        camera={{ position: [0, 0, 7.4], fov: 45 }}
        gl={{ antialias: true, alpha: true }}
        dpr={[1, 1.75]}
      >
        <color attach="background" args={["#060814"]} />
        <Scene onToggleTalk={toggleTalk} />
      </Canvas>
      <div className="accent-wash pointer-events-none absolute inset-0" />

      <div className="pointer-events-none absolute inset-0 flex flex-col">
        <TopBar panel={panel} setPanel={setPanel} />

        <main className="flex min-h-0 flex-1 gap-4 px-4 pb-4">
          <div className={`${RAIL} left-4 ${panel === "left" ? "flex" : "hidden"} min-[1180px]:flex`}>
            <LeftRail submit={submit} send={send} onToggleTalk={toggleTalk} />
          </div>

          <Conversation submit={submit} onToggleTalk={toggleTalk} />

          <div className={`${RAIL} right-4 ${panel === "right" ? "flex" : "hidden"} min-[1180px]:flex`}>
            <RightRail />
          </div>
        </main>
      </div>

      <Toasts />
      <DevPanel send={send} submit={submit} onToggleTalk={toggleTalk} />
      <ScreenshotPanel />
      <ConfirmModal send={send} />
    </div>
  );
}
