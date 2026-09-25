import { useRef, useMemo } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { vertex, fragment } from "./orb.glsl";
import { useUltron } from "../store/ultronStore";

const PHASE_COLOR = {
  idle: new THREE.Color("#1e6cff"),
  listening: new THREE.Color("#00e5ff"),
  thinking: new THREE.Color("#a855f7"),
  acting: new THREE.Color("#f59e0b"),
  speaking: new THREE.Color("#22d3ee"),
  error: new THREE.Color("#ef4444"),
};

export default function Orb({ onToggleTalk }) {
  const mesh = useRef();

  const uniforms = useMemo(
    () => ({
      uTime: { value: 0 },
      uAmp: { value: 0 },
      uColor: { value: new THREE.Color("#1e6cff") },
    }),
    []
  );

  useFrame((state, delta) => {
    // Amplitude: window.__ultronMicRms is the local mic level (useAudioLevel);
    // window.__ultronRms (TTS level) is fed by the backend in Phase 7.
    const s = useUltron.getState();
    const phase = s.previewPhase ?? s.phase;
    uniforms.uTime.value += delta;
    uniforms.uColor.value.lerp(PHASE_COLOR[phase] ?? PHASE_COLOR.idle, Math.min(1, delta * 3.2));
    const target = phase === "speaking" ? (window.__ultronRms ?? 0) : (window.__ultronMicRms ?? 0);
    uniforms.uAmp.value += (target - uniforms.uAmp.value) * 0.18;
    mesh.current.rotation.y += delta * (phase === "thinking" ? 0.55 : 0.12);
  });

  return (
    <mesh
      ref={mesh}
      onClick={onToggleTalk}
      onPointerOver={() => (document.body.style.cursor = "pointer")}
      onPointerOut={() => (document.body.style.cursor = "default")}
    >
      <icosahedronGeometry args={[1.7, 64]} />
      <shaderMaterial
        vertexShader={vertex}
        fragmentShader={fragment}
        uniforms={uniforms}
        transparent
      />
    </mesh>
  );
}
