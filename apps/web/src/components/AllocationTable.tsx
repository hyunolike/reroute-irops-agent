"use client";

import { useMemo, useState } from "react";
import { Accessibility, ArrowRight, Crown, Link2, Users } from "lucide-react";
import { Panel, Pill, PolicyChip } from "./ui";
import { cx, hm } from "@/lib/format";
import type { Plan, PlanItem } from "@/lib/types";

const STATUS: Record<PlanItem["status"], { label: string; tone: "nv" | "amber" | "red" | "green" | "sky" | "slate" }> = {
  AUTO_ASSIGNED: { label: "Auto", tone: "nv" },
  MANUAL_REVIEW: { label: "Review", tone: "amber" },
  NO_FEASIBLE: { label: "No feasible", tone: "red" },
  EXECUTED: { label: "Rebooked", tone: "green" },
  EXECUTION_FAILED: { label: "Failed", tone: "red" },
  HELD_FOR_OPERATOR: { label: "Held", tone: "amber" },
};

const TABS = [
  { key: "all", label: "All" },
  { key: "auto", label: "Auto" },
  { key: "review", label: "Review" },
  { key: "none", label: "No feasible" },
] as const;

export function AllocationTable({
  plan,
  hoverPolicy,
  setHoverPolicy,
  selectedManual,
  toggleManual,
  approvalOpen,
}: {
  plan: Plan;
  hoverPolicy: string | null;
  setHoverPolicy: (id: string | null) => void;
  selectedManual: string[];
  toggleManual: (id: string) => void;
  approvalOpen: boolean;
}) {
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("all");
  const items = useMemo(() => {
    const f = (i: PlanItem) =>
      tab === "all" ||
      (tab === "auto" && ["AUTO_ASSIGNED", "EXECUTED"].includes(i.status)) ||
      (tab === "review" && ["MANUAL_REVIEW", "HELD_FOR_OPERATOR"].includes(i.status)) ||
      (tab === "none" && i.status === "NO_FEASIBLE");
    return plan.items.filter(f);
  }, [plan.items, tab]);

  return (
    <Panel
      title="Passenger Allocation"
      subtitle="Solver output is the source of truth — the LLM cannot change it"
      icon={<Users className="h-4 w-4" />}
      right={
        <div className="flex gap-1 rounded-lg border border-ops-line p-0.5">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={cx("rounded-md px-2.5 py-1 text-xs", tab === t.key ? "bg-nv text-black font-semibold" : "text-ops-muted hover:text-white")}
            >
              {t.label}
            </button>
          ))}
        </div>
      }
      bodyClassName="p-0"
    >
      <div className="max-h-[520px] overflow-auto">
        <table className="w-full min-w-[980px] text-left text-xs">
          <thead className="sticky top-0 z-10 bg-ops-panel text-[10px] uppercase tracking-wider text-ops-muted">
            <tr>
              <th className="px-3 py-2 font-medium">Passenger</th>
              <th className="px-2 py-2 font-medium">Tier</th>
              <th className="px-2 py-2 font-medium">Cabin</th>
              <th className="px-2 py-2 font-medium">Original → Alternative</th>
              <th className="px-2 py-2 text-right font-medium">Delay</th>
              <th className="px-2 py-2 text-right font-medium">Cost</th>
              <th className="px-2 py-2 font-medium">Status</th>
              <th className="px-2 py-2 font-medium">Reason &amp; policy evidence</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i) => {
              const conn = i.penalties?.is_connection;
              const ssr = i.penalties?.special_assistance;
              const downgraded = i.original_cabin === "BUSINESS" && i.new_cabin === "ECONOMY";
              const s = STATUS[i.status];
              return (
                <tr key={i.id} className={cx("border-t border-ops-line/60 align-top hover:bg-ops-panel2/60", i.policy_ids.includes(hoverPolicy ?? "") && "bg-nv/5")}>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-1.5 font-medium text-slate-100">
                      {i.vip && <Crown className="h-3.5 w-3.5 text-amber-300" aria-label="VIP" />}
                      {i.passenger_name}
                    </div>
                    <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-ops-muted">
                      <span className="font-mono">{i.passenger_id}</span>
                      {conn && (
                        <span className="flex items-center gap-0.5 text-sky-300">
                          <Link2 className="h-3 w-3" /> connection
                        </span>
                      )}
                      {ssr && (
                        <span className="flex items-center gap-0.5 text-amber-300">
                          <Accessibility className="h-3 w-3" /> {ssr}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-2 py-2 text-slate-300">{i.tier}</td>
                  <td className="px-2 py-2">
                    <span className="font-mono text-slate-300">{i.original_cabin[0]}</span>
                    {i.new_cabin && (
                      <>
                        <ArrowRight className="mx-0.5 inline h-3 w-3 text-ops-muted" />
                        <span className={cx("font-mono", downgraded ? "font-bold text-amber-300" : "text-slate-300")}>{i.new_cabin[0]}</span>
                      </>
                    )}
                  </td>
                  <td className="px-2 py-2 font-mono">
                    <span className="text-rose-300/70 line-through">{i.original_flight}</span>
                    <ArrowRight className="mx-1 inline h-3 w-3 text-ops-muted" />
                    <span className={i.alternative_flight ? "font-semibold text-white" : "text-rose-300"}>{i.alternative_flight ?? "—"}</span>
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-slate-300">{hm(i.delay_minutes)}</td>
                  <td className="px-2 py-2 text-right font-mono text-ops-muted" title={JSON.stringify(i.penalties, null, 1)}>
                    {Math.round(i.score).toLocaleString()}
                  </td>
                  <td className="px-2 py-2">
                    <Pill tone={s.tone}>{s.label}</Pill>
                    {i.status === "MANUAL_REVIEW" && approvalOpen && i.alternative_flight && (
                      <label className="mt-1.5 flex cursor-pointer items-center gap-1 text-[10px] text-amber-200">
                        <input type="checkbox" className="accent-[#76B900]" checked={selectedManual.includes(i.id)} onChange={() => toggleManual(i.id)} />
                        include in approval
                      </label>
                    )}
                  </td>
                  <td className="max-w-[420px] px-2 py-2">
                    <div className="leading-snug text-slate-300">{i.reason}</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {i.policy_ids.map((p) => (
                        <PolicyChip key={p} id={p} active={hoverPolicy === p} onHover={setHoverPolicy} />
                      ))}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
