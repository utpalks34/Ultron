import { useRef, useMemo } from "react";
import { useFrame } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import * as THREE from "three";
import { useUltron } from "../store/ultronStore";

const FAILED = new THREE.Color("#ef4444");

export default function AgentNode({ name, label, position, color }) {
  const mesh = useRef();
  const mat = useRef();
  const labelEl = useRef();
  const labelIdle = useRef(true);
  const base = useMemo(() => new THREE.Color(color), [color]);

  useFrame((state, delta) => {
    const n = useUltron.getState().nodes[name];
    const age = n ? (Date.now() - n.ts) / 1000 : Infinity;
    const t = state.clock.elapsedTime;
    const loading = useUltron.getState().modelPhase?.phase === "loading";

    let intensity = 0.25;
    let scale = 1;
    let tint = base;
    if (n?.status === "active") {
      intensity = loading ? 1.4 + 0.9 * Math.sin(t * 16) : 1.9 + 0.6 * Math.sin(t * 7);
      scale = 1.3;
    } else if (n?.status === "done") {
      intensity = 0.25 + 1.4 * Math.exp(-age * 2.2);
      scale = 1 + 0.25 * Math.exp(-age * 2.2);
    } else if (n?.status === "failed") {
      tint = FAILED;
      intensity = age < 3 ? 1.8 : 0.5;
      scale = 1.15;
    }

    // idle agents recede; the class only flips when the status changes
    const idle = !n || n.status === "idle";
    if (labelEl.current && idle !== labelIdle.current) {
      labelIdle.current = idle;
      labelEl.current.classList.toggle("opacity-40", idle);
      labelEl.current.classList.toggle("opacity-100", !idle);
    }

    const k = Math.min(1, delta * 10);
    mat.current.emissiveIntensity += (intensity - mat.current.emissiveIntensity) * k;
    mat.current.emissive.lerp(tint, k);
    const s = mesh.current.scale.x;
    mesh.current.scale.setScalar(s + (scale - s) * k);
  });

  return (
    <group position={position}>
      <mesh ref={mesh}>
        <sphereGeometry args={[0.17, 32, 32]} />
        <meshStandardMaterial
          ref={mat}
          color="#0a1020"
          emissive={color}
          emissiveIntensity={0.25}
          roughness={0.4}
        />
      </mesh>
      <Html center position={[0, -0.36, 0]} pointerEvents="none" zIndexRange={[0, 0]}>
        <div
          ref={labelEl}
          className="label text-[11px] whitespace-nowrap opacity-40 transition-opacity duration-300"
          style={{ textShadow: "0 0 10px rgba(34,211,238,0.55)" }}
        >
          {label}
        </div>
      </Html>
    </group>
  );
}
