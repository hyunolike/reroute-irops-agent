import { AlertOctagon, Crown, Link2, Plane, Accessibility, Users } from "lucide-react";
import { Panel, Pill } from "./ui";
import { hhmm } from "@/lib/format";
import type { AgentEvent, Flight } from "@/lib/types";

export function DisruptionCard({ events }: { events: AgentEvent[] }) {
  const flightEv = events.find((e) => e.type === "TOOL_RESULT" && e.detail.tool === "get_disrupted_flight");
  const paxEv = events.find((e) => e.type === "TOOL_RESULT" && e.detail.tool === "get_affected_passengers");
  const f = flightEv?.detail.flight as Flight | undefined;
  const pax = (paxEv?.detail.passengers ?? []) as { cabin: string; vip: boolean; onward_flight_no: string | null; special_assistance: string | null }[];

  if (!f) {
    return (
      <Panel title="Disruption" icon={<AlertOctagon className="h-4 w-4" />}>
        <div className="text-sm text-ops-muted">에이전트가 운항 정보를 조회하면 표시됩니다.</div>
      </Panel>
    );
  }
  const tone = f.status === "CANCELLED" ? "red" : f.status === "DELAYED" ? "amber" : "green";
  return (
    <Panel title="Disruption" icon={<AlertOctagon className="h-4 w-4" />} right={<Pill tone={tone}>{f.status}</Pill>}>
      <div className="flex items-end justify-between">
        <div>
          <div className="font-mono text-3xl font-bold tracking-tight">{f.flight_no}</div>
          <div className="mt-1 flex items-center gap-2 text-lg font-semibold text-slate-200">
            {f.origin} <Plane className="h-4 w-4 text-nv" /> {f.destination}
          </div>
        </div>
        <div className="text-right text-xs text-ops-muted">
          <div>
            STD <span className="font-mono text-slate-200">{hhmm(f.departure_time)}</span> · STA{" "}
            <span className="font-mono text-slate-200">{hhmm(f.arrival_time)}</span>
          </div>
          <div>{f.aircraft}</div>
        </div>
      </div>
      {f.disruption && (
        <div className="mt-3 rounded-lg border border-rose-500/20 bg-rose-500/5 px-3 py-2 text-xs text-rose-200">
          {f.disruption.reason}
          {f.disruption.delay_minutes ? ` · +${f.disruption.delay_minutes}min` : ""}
          {f.disruption.airline_fault && <span className="ml-1 text-rose-300/70">(airline fault)</span>}
        </div>
      )}
      {pax.length > 0 && (
        <div className="mt-3 grid grid-cols-4 gap-2 text-center">
          <Stat icon={<Users className="h-3.5 w-3.5" />} value={pax.length} label="affected" strong />
          <Stat icon={<Crown className="h-3.5 w-3.5" />} value={pax.filter((p) => p.vip).length} label="VIP" />
          <Stat icon={<Link2 className="h-3.5 w-3.5" />} value={pax.filter((p) => p.onward_flight_no).length} label="connections" />
          <Stat icon={<Accessibility className="h-3.5 w-3.5" />} value={pax.filter((p) => p.special_assistance).length} label="SSR" />
        </div>
      )}
    </Panel>
  );
}

function Stat({ icon, value, label, strong }: { icon: React.ReactNode; value: number; label: string; strong?: boolean }) {
  return (
    <div className="rounded-lg border border-ops-line bg-ops-panel2 px-1 py-2">
      <div className={strong ? "text-2xl font-bold text-white" : "text-xl font-bold text-slate-200"}>{value}</div>
      <div className="flex items-center justify-center gap-1 text-[10px] uppercase tracking-wide text-ops-muted">
        {icon}
        {label}
      </div>
    </div>
  );
}
