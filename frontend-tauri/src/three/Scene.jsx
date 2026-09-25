import { Suspense, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { EffectComposer, Bloom } from "@react-three/postprocessing";
import { MathUtils } from "three";
import Orb from "./Orb";
import AgentGraph from "./AgentGraph";
import { useUltron } from "../store/ultronStore";

// The orb sits in the "stage" at the top of the centre column (see hud/Conversation.jsx).
// The stage is TOPBAR_PX below the top edge and CSS gives it clamp(220px, 38vh, 380px);
// stageHeight() must stay the same formula. The orb's centre and radius are derived from it
// every frame, so it stays put when the window is resized.
export const TOPBAR_PX = 48;
export const stageHeight = (h) => Math.min(380, Math.max(220, h * 0.38));
const ORB_RADIUS = 1.7; // icosahedronGeometry radius in Orb.jsx
const STAGE_FILL = 0.34; // orb radius as a share of the stage height

export default function Scene({ onToggleTalk }) {
  const devMode = useUltron((s) => s.devMode);
  const group = useRef();

  useFrame((state) => {
    const g = group.current;
    if (!g) return;
    const { height } = state.size;
    const cam = state.camera;
    const visibleH = 2 * cam.position.z * Math.tan(MathUtils.degToRad(cam.fov / 2));
    const stage = stageHeight(height);
    const centreY = TOPBAR_PX + stage / 2;
    g.position.x = 0;
    g.position.y = ((height / 2 - centreY) / height) * visibleH;
    g.scale.setScalar((((stage * STAGE_FILL) / height) * visibleH) / ORB_RADIUS);
  });

  return (
    <>
      <ambientLight intensity={0.35} />
      <pointLight position={[4, 4, 4]} intensity={1.1} />
      <Suspense fallback={null}>
        <group ref={group}>
          <Orb onToggleTalk={onToggleTalk} />
          {devMode && <AgentGraph />}
        </group>
      </Suspense>
      <EffectComposer>
        <Bloom intensity={1.15} luminanceThreshold={0.22} mipmapBlur />
      </EffectComposer>
      <OrbitControls enablePan={false} enableZoom={false} enableRotate={false} />
    </>
  );
}
