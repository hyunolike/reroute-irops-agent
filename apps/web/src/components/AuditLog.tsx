"use client";

import { useCallback, useEffect, useState } from "react";
import { ScrollText } from "lucide-react";
import { Panel, Pill } from "./ui";
import { api } from "@/lib/api";
import { clock, cx } from "@/lib/format";
import type { AuditEntry } from "@/lib/types";

export function AuditLog({ refreshKey }: { refreshKey: number }) {
  const [rows, setRows] = useState<AuditEntry[]>([]);
  const [filter, setFilter] = useState<"all" | "DENY" | "booking">("all");
  const load = useCallback(() => api.audit(150).then((r) => setRows(r.entries)).catch(() => {}), []);
  useEffect(() => {
    load();
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [load, refreshKey]);

  const shown = rows.filter((r) => filter === "all" || (filter === "DENY" ? r.result === "DENY" : r.action.startsWith("booking") || r.action.startsWith("plan")));
  const denies = rows.filter((r) => r.result === "DENY").length;
  return (
    <Panel
      title="Audit Log"
      subtitle={`${rows.length} entries · ${denies} denied · every egress, approval and booking write`}
      icon={<ScrollText className="h-4 w-4" />}
      right={
        <div className="flex gap-1 rounded-lg border border-ops-line p-0.5 text-xs">
          {(["all", "DENY", "booking"] as const).map((f) => (
            <button key={f} onClick={() => setFilter(f)} className={cx("rounded-md px-2 py-1", filter === f ? "bg-nv font-semibold text-black" : "text-ops-muted hover:text-white")}>
              {f === "all" ? "All" : f === "DENY" ? "Denied" : "Approvals & bookings"}
            </button>
          ))}
        </div>
      }
      bodyClassName="p-0"
    >
      <div className="max-h-[420px] overflow-auto">
        <table className="w-full min-w-[900px] text-left text-[11px]">
          <thead className="sticky top-0 bg-ops-panel text-[10px] uppercase tracking-wider text-ops-muted">
            <tr>
              {["timestamp", "agent", "tool", "action", "target", "policy", "result", "enforced by"].map((h) => (
                <th key={h} className="px-3 py-2 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="font-mono">
            {shown.map((r) => (
              <tr key={r.id} className={cx("border-t border-ops-line/50", r.result === "DENY" && "bg-rose-500/5")}>
                <td className="whitespace-nowrap px-3 py-1.5 text-ops-muted">{clock(r.timestamp)}</td>
                <td className="px-3 py-1.5 text-slate-300">{r.agent}</td>
                <td className="px-3 py-1.5 text-sky-300">{r.tool}</td>
                <td className="px-3 py-1.5 text-slate-400">{r.action}</td>
                <td className="max-w-[340px] truncate px-3 py-1.5 text-slate-200" title={r.target}>
                  {r.target}
                </td>
                <td className="px-3 py-1.5 text-slate-400">{r.policy}</td>
                <td className="px-3 py-1.5">
                  <Pill tone={r.result === "DENY" || r.result === "FAILURE" ? "red" : "green"}>{r.result}</Pill>
                </td>
                <td className="px-3 py-1.5 text-slate-400">{r.enforced_by}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
