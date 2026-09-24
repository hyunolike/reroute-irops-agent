import { ArrowRight, BarChart3, Cpu, Scale } from "lucide-react";
import { Panel, Pill } from "./ui";
import { cx, hm } from "@/lib/format";
import type { Plan } from "@/lib/types";

export function Kpis({ plan }: { plan: Plan }) {
  const s = plan.summary;
  const cards = [
    { label: "Affected", ko: "영향 승객", value: s.affected, cls: "text-white" },
    { label: "Auto-assigned", ko: "자동 배정", value: s.auto_assigned, cls: "text-nv-light" },
    { label: "Manual review", ko: "운영자 검토", value: s.manual_review, cls: "text-amber-300" },
    { label: "No feasible", ko: "대안 없음", value: s.no_feasible, cls: "text-rose-300" },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
      {cards.map((c) => (
        <div key={c.label} className="rounded-xl border border-ops-line bg-ops-panel p-3">
          <div className="text-[11px] uppercase tracking-wider text-ops-muted">{c.label}</div>
          <div className={cx("mt-1 text-4xl font-bold tabular-nums", c.cls)}>{c.value}</div>
          <div className="text-xs text-ops-muted">{c.ko}</div>
        </div>
      ))}
      <div className="col-span-2 rounded-xl border border-ops-line bg-ops-panel p-3">
        <div className="flex items-center justify-between">
          <div className="text-[11px] uppercase tracking-wider text-ops-muted">Solver</div>
          <Pill tone={s.nvidia ? "nv" : "amber"}>{s.nvidia ? "NVIDIA cuOpt" : "CPU fallback"}</Pill>
        </div>
        <div className="mt-1 flex items-center gap-2 text-sm font-semibold text-slate-100">
          <Cpu className="h-4 w-4 text-nv" /> {s.status} · {s.solve_time_ms.toFixed(0)} ms
        </div>
        <div className="mt-1 font-mono text-[11px] text-ops-muted">
          {s.variables} binary vars × {s.constraints} constraints · objective {plan.objective_value.toLocaleString()}
        </div>
        <div className="mt-1 text-[11px] text-ops-muted">
          Avg delay <span className="text-slate-200">{hm(s.avg_delay_minutes)}</span> · VIP <span className="text-slate-200">{hm(s.vip_avg_delay_minutes)}</span>
        </div>
        {s.notes?.map((n) => (
          <div key={n} className="mt-1 text-[11px] text-amber-300">
            {n}
          </div>
        ))}
      </div>
    </div>
  );
}

export function BaselineComparison({ plan }: { plan: Plan }) {
  const b = plan.baseline;
  const s = plan.summary;
  const rows = [
    { label: "연결편 놓침 (Missed connections)", base: b.missed_connections, ours: 0, fmt: String, better: "lower" },
    { label: "특수지원 승객 규정 위반 (SSR)", base: b.ssr_violations, ours: 0, fmt: String, better: "lower" },
    { label: "비즈니스 다운그레이드", base: b.business_downgrades, ours: s.business_downgrades, fmt: String, better: "lower" },
    { label: "VIP 평균 지연", base: b.vip_avg_delay_minutes, ours: s.vip_avg_delay_minutes, fmt: hm, better: "lower" },
    { label: "전체 평균 지연", base: b.avg_delay_minutes, ours: s.avg_delay_minutes, fmt: hm, better: "lower" },
  ];
  return (
    <Panel title="Why an optimizer?" subtitle={`vs. ${b.method}`} icon={<Scale className="h-4 w-4" />}>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-wider text-ops-muted">
            <th className="pb-2 font-medium">Metric</th>
            <th className="pb-2 text-right font-medium">선착순 수작업</th>
            <th className="pb-2" />
            <th className="pb-2 text-right font-medium text-nv">ReRoute</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const improved = r.ours < r.base;
            const worse = r.ours > r.base;
            return (
              <tr key={r.label} className="border-t border-ops-line/60">
                <td className="py-2 text-slate-300">{r.label}</td>
                <td className="py-2 text-right font-mono text-rose-200/80">{r.fmt(r.base)}</td>
                <td className="px-2 py-2 text-center text-ops-muted">
                  <ArrowRight className="inline h-3.5 w-3.5" />
                </td>
                <td className={cx("py-2 text-right font-mono font-bold", improved ? "text-nv-light" : worse ? "text-amber-300" : "text-slate-200")}>{r.fmt(r.ours)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-3 text-[11px] leading-relaxed text-ops-muted">
        두 방식 모두 정책상 허용된 동일한 항공편만 사용합니다. 전체 평균 지연이 소폭 늘 수 있는 것은 연결편·VIP·특수지원 승객을 보호하기 위한
        <span className="text-slate-300"> 의도된 trade-off</span>이며, 가중치는 <code className="text-slate-300">config/optimization.yaml</code>에서 조정합니다.
      </p>
    </Panel>
  );
}

export function FlightLoads({ plan }: { plan: Plan }) {
  const loads = Object.entries(plan.summary.flight_loads);
  return (
    <Panel title="Seat allocation by flight" subtitle="Capacity constraint (C2) — never exceeded" icon={<BarChart3 className="h-4 w-4" />}>
      <div className="space-y-3">
        {loads.map(([fno, l]) => (
          <div key={fno}>
            <div className="mb-1 flex items-center justify-between text-xs">
              <span className="font-mono font-semibold text-slate-100">{fno}</span>
              <span className="text-ops-muted">
                J {l.business_assigned}/{l.business_available} · Y {l.economy_assigned}/{l.economy_available}
              </span>
            </div>
            <Bar used={l.business_assigned} cap={l.business_available} cls="bg-violet-400" label="J" />
            <Bar used={l.economy_assigned} cap={l.economy_available} cls="bg-nv" label="Y" />
          </div>
        ))}
      </div>
      {plan.summary.excluded_flights.length > 0 && (
        <div className="mt-4 border-t border-ops-line pt-3">
          <div className="mb-2 text-[11px] uppercase tracking-wider text-ops-muted">Excluded by constraints</div>
          <div className="space-y-1.5">
            {plan.summary.excluded_flights.map((e) => (
              <div key={e.flight_no} className="flex items-center gap-2 text-xs">
                <span className="w-14 font-mono text-slate-400 line-through">{e.flight_no}</span>
                <Pill tone="red">{e.constraint}</Pill>
                {e.policy_id && <span className="font-mono text-[10px] text-nv-light">{e.policy_id}</span>}
                <span className="truncate text-ops-muted">{e.reason}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Panel>
  );
}

function Bar({ used, cap, cls, label }: { used: number; cap: number; cls: string; label: string }) {
  const pct = cap ? Math.min(100, (used / cap) * 100) : 0;
  return (
    <div className="mb-1 flex items-center gap-2">
      <span className="w-3 text-[10px] text-ops-muted">{label}</span>
      <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-ops-panel2">
        <div className={cx("h-full rounded-full transition-all duration-700", cls)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
