import { useRef, useMemo } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { useUltron } from "../store/ultronStore";

export default function DataBeam({ from, to, target }) {
  const line = useRef();
  const dot = useRef();
  const a = useMemo(() => new THREE.Vector3(...from), [from]);
  const b = useMemo(() => new THREE.Vector3(...to), [to]);
  const geom = useMemo(() => new THREE.BufferGeometry().setFromPoints([a, b]), [a, b]);

  useFrame((state) => {
    const n = useUltron.getState().nodes[target];
    const age = n ? (Date.now() - n.ts) / 1000 : Infinity;

    let lineOpacity = 0.1;
    let visible = false;
    let t = 0;
    if (n?.status === "active") {
      lineOpacity = 0.55;
      visible = true;
      t = (state.clock.elapsedTime * 1.1) % 1;   // supervisor -> worker
    } else if (n?.status === "done" && age < 0.9) {
      lineOpacity = 0.4;
      visible = true;
      t = 1 - age / 0.9;                          // worker -> supervisor return beam
    }

    dot.current.visible = visible;
    if (visible) dot.current.position.lerpVectors(a, b, t);
    const m = line.current.material;
    m.opacity += (lineOpacity - m.opacity) * 0.2;
  });

  return (
    <group>
      <line ref={line} geometry={geom}>
        <lineBasicMaterial color="#67e8f9" transparent opacity={0.1} />
      </line>
      <mesh ref={dot} visible={false}>
        <sphereGeometry args={[0.05, 12, 12]} />
        <meshBasicMaterial color="#a5f3fc" />
      </mesh>
    </group>
  );
}
