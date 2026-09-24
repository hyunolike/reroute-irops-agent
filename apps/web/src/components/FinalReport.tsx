import { ClipboardCheck, PlaneTakeoff } from "lucide-react";
import { Panel } from "./ui";
import type { Task } from "@/lib/types";

export function FinalReport({ task }: { task: Task }) {
  const r = task.report;
  if (!r) return null;
  if (r.outcome === "NO_ACTION_REQUIRED")
    return (
      <Panel title="Agent Decision" icon={<ClipboardCheck className="h-4 w-4" />} className="border-nv/40">
        <div className="text-lg font-semibold text-white">재배정 불필요 — {r.flight_no} ({r.status})</div>
        <p className="mt-2 text-sm text-slate-300">{r.reasoning}</p>
        <p className="mt-2 text-xs text-ops-muted">에이전트가 규정({(r.policies ?? []).join(", ") || "—"})을 검색해 스스로 판단하고 작업을 종료했습니다. 불필요한 예약 변경을 만들지 않는 것도 Agent의 역할입니다.</p>
      </Panel>
    );
  if (r.outcome === "REJECTED")
    return (
      <Panel title="Final Report" icon={<ClipboardCheck className="h-4 w-4" />}>
        <div className="text-sm text-slate-200">운영자가 재배정안을 반려했습니다. 어떤 예약도 변경되지 않았습니다.</div>
      </Panel>
    );
  return (
    <Panel title="Final Report" subtitle="Bookings re-issued through the Airline Booking API" icon={<ClipboardCheck className="h-4 w-4" />} className="border-nv/50 shadow-[0_0_40px_-15px_rgba(118,185,0,0.7)]">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Big v={r.rebooked} l="rebooked" cls="text-nv-light" />
        <Big v={r.held_for_operator} l="held for operator" cls="text-amber-300" />
        <Big v={r.no_feasible} l="alternative handling" cls="text-rose-300" />
        <Big v={r.failed} l="failed" cls="text-slate-300" />
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {Object.entries(r.by_flight ?? {}).map(([f, n]) => (
          <span key={f} className="flex items-center gap-1.5 rounded-lg border border-ops-line bg-ops-panel2 px-2.5 py-1 text-xs">
            <PlaneTakeoff className="h-3.5 w-3.5 text-nv" /> <span className="font-mono text-white">{f}</span> {String(n)} pax
          </span>
        ))}
      </div>
      <div className="mt-3 text-[11px] uppercase tracking-wider text-ops-muted">Follow-ups</div>
      <ul className="mt-1 space-y-1 text-xs text-slate-300">
        {(r.follow_ups ?? []).map((f: string) => (
          <li key={f} className="flex gap-2">
            <span className="text-nv">›</span>
            {f}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function Big({ v, l, cls }: { v: number; l: string; cls: string }) {
  return (
    <div className="rounded-lg bg-ops-panel2 p-3">
      <div className={`text-3xl font-bold ${cls}`}>{v}</div>
      <div className="text-xs text-ops-muted">{l}</div>
    </div>
  );
}
