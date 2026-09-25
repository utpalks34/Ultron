import { useMemo } from "react";
import AgentNode from "./AgentNode";
import DataBeam from "./DataBeam";

// The names MUST match settings.graph_nodes in backend-ai/config.py exactly.
const NODES = [
  { name: "supervisor", label: "SUPERVISOR", color: "#a855f7" },
  { name: "dev_agent", label: "DEV", color: "#22d3ee" },
  { name: "web_agent", label: "WEB", color: "#3b82f6" },
  { name: "os_agent", label: "OS", color: "#f59e0b" },
  { name: "rag_agent", label: "RAG", color: "#34d399" },
];

export default function AgentGraph({ rx = 2.3, ry = 1.55 }) {
  const positions = useMemo(
    () =>
      NODES.map((_, i) => {
        const angle = Math.PI / 2 - i * ((2 * Math.PI) / NODES.length);
        return [rx * Math.cos(angle), ry * Math.sin(angle), 0];
      }),
    [rx, ry]
  );

  return (
    <group>
      {NODES.map((n, i) => (
        <AgentNode key={n.name} name={n.name} label={n.label} position={positions[i]} color={n.color} />
      ))}
      {NODES.slice(1).map((n, i) => (
        <DataBeam key={n.name} from={positions[0]} to={positions[i + 1]} target={n.name} />
      ))}
    </group>
  );
}
