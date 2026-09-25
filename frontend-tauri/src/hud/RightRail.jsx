import { useUltron } from "../store/ultronStore";
import VramGauge from "./VramGauge";
import { AGENT_LABEL, PHASE_LABEL } from "./phase";

const AGENTS = ["supervisor", "dev_agent", "web_agent", "os_agent", "rag_agent"];

const DOT = {
  active: "bg-accent animate-pulse motion-reduce:animate-none",
  done: "bg-emerald-400",
  failed: "bg-red-400",
  idle: "bg-line",
};

function AgentRow({ name }) {
  const n = useUltron((s) => s.nodes[name]);
  const status = n?.status ?? "idle";
  const note =
    status === "active" ? "working" : status === "failed" ? "failed" : status === "done" && n?.ms != null ? `${n.ms} ms` : "";

  return (
    <li
      className={`flex items-center gap-3 rounded-lg px-2.5 py-1.5 ${status === "active" ? "bg-accent/10" : ""}`}
    >
      <span className={`h-2 w-2 shrink-0 rounded-full ${DOT[status] ?? DOT.idle}`} />
      <span className="flex-1 text-[13px]">{AGENT_LABEL[name]}</span>
      <span className="font-mono text-[11px] text-muted">{note}</span>
    </li>
  );
}

// Live system status. Everything here is read from the store; nothing is invented.
export default function RightRail() {
  const phase = useUltron((s) => s.previewPhase ?? s.phase);
  const activeModel = useUltron((s) => s.activeModel);
  const activity = useUltron((s) => s.activity);
  const errors = useUltron((s) => s.errors);
  const lastError = errors[errors.length - 1];

  return (
    <div className="flex h-full w-full flex-col gap-3 overflow-y-auto pb-1">
      <section className="card-flat p-4">
        <div className="flex items-center gap-2.5">
          <span
            className="h-2.5 w-2.5 shrink-0 rounded-full bg-accent"
            style={{ boxShadow: "0 0 12px rgb(var(--accent-rgb) / 0.8)" }}
          />
          <span className="font-display text-[22px] font-semibold tracking-tight">
            {PHASE_LABEL[phase] ?? phase}
          </span>
        </div>
        <div className="mt-2 truncate font-mono text-xs text-muted">{activeModel ?? "No model loaded"}</div>
      </section>

      <section className="card-flat p-2">
        <h2 className="label px-2.5 pb-1 pt-2">Agents</h2>
        <ul>
          {AGENTS.map((name) => (
            <AgentRow key={name} name={name} />
          ))}
        </ul>
      </section>

      <VramGauge />

      <section className="card-flat p-4">
        <h2 className="label">Recent activity</h2>
        {activity.length === 0 ? (
          <div className="mt-2 text-xs text-muted/80">No tool calls yet.</div>
        ) : (
          <ul className="mt-2 space-y-1 font-mono text-[11px]">
            {activity.slice(-6).map((a, i) => (
              <li key={i} className="truncate text-fg/80">
                <span className="text-muted">{a.agent ?? "-"}/</span>
                {a.tool} {a.preview}
              </li>
            ))}
          </ul>
        )}
        {lastError && (
          <div className="mt-3 line-clamp-2 break-words border-t border-line/40 pt-2 text-xs text-red-300">
            {lastError.source}: {lastError.message}
          </div>
        )}
      </section>
    </div>
  );
}
