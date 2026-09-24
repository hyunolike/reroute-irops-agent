import type { ReactNode } from "react";
import { cx } from "@/lib/format";
import type { Component } from "@/lib/types";

export function Panel({
  title,
  subtitle,
  icon,
  right,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  icon?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={cx("rounded-xl border border-ops-line bg-ops-panel/90 shadow-[0_0_0_1px_rgba(255,255,255,0.02)]", className)}>
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-ops-line px-4 py-3">
          <div className="flex min-w-0 items-center gap-2">
            {icon && <span className="text-nv">{icon}</span>}
            <div className="min-w-0">
              <h2 className="truncate text-[13px] font-semibold uppercase tracking-wider text-slate-200">{title}</h2>
              {subtitle && <p className="truncate text-xs text-ops-muted">{subtitle}</p>}
            </div>
          </div>
          {right}
        </header>
      )}
      <div className={cx("p-4", bodyClassName)}>{children}</div>
    </section>
  );
}

const COMPONENT_STYLE: Record<Component, { label: string; cls: string; nvidia?: boolean }> = {
  nemotron: { label: "Nemotron · NIM", cls: "bg-nv/15 text-nv-light ring-nv/40", nvidia: true },
  "mock-llm": { label: "Mock planner", cls: "bg-amber-500/10 text-amber-300 ring-amber-500/30" },
  "nemo-retriever": { label: "NeMo Retriever", cls: "bg-nv/15 text-nv-light ring-nv/40", nvidia: true },
  "lexical-retriever": { label: "BM25 fallback", cls: "bg-amber-500/10 text-amber-300 ring-amber-500/30" },
  cuopt: { label: "cuOpt", cls: "bg-nv/15 text-nv-light ring-nv/40", nvidia: true },
  "fallback-solver": { label: "HiGHS fallback", cls: "bg-amber-500/10 text-amber-300 ring-amber-500/30" },
  openshell: { label: "OpenShell", cls: "bg-nv/15 text-nv-light ring-nv/40", nvidia: true },
  "policy-mirror": { label: "Policy mirror", cls: "bg-sky-500/10 text-sky-300 ring-sky-500/30" },
  "airline-api": { label: "Airline API", cls: "bg-slate-500/10 text-slate-300 ring-slate-500/30" },
  "approval-gateway": { label: "Approval Gateway", cls: "bg-violet-500/10 text-violet-300 ring-violet-500/30" },
  orchestrator: { label: "Orchestrator", cls: "bg-slate-500/10 text-slate-400 ring-slate-500/20" },
  "external-agent": { label: "External agent · MCP", cls: "bg-fuchsia-500/10 text-fuchsia-300 ring-fuchsia-500/30" },
};

export function ComponentBadge({ c, className }: { c: Component; className?: string }) {
  const s = COMPONENT_STYLE[c] ?? COMPONENT_STYLE.orchestrator;
  return (
    <span className={cx("inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1", s.cls, className)}>
      {s.nvidia && <span className="h-1.5 w-1.5 rounded-full bg-nv" />}
      {s.label}
    </span>
  );
}

export function Pill({ tone, children, className }: { tone: "green" | "amber" | "red" | "sky" | "slate" | "violet" | "nv"; children: ReactNode; className?: string }) {
  const tones = {
    green: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30",
    amber: "bg-amber-500/10 text-amber-300 ring-amber-500/30",
    red: "bg-rose-500/10 text-rose-300 ring-rose-500/30",
    sky: "bg-sky-500/10 text-sky-300 ring-sky-500/30",
    slate: "bg-slate-500/10 text-slate-300 ring-slate-500/30",
    violet: "bg-violet-500/10 text-violet-300 ring-violet-500/30",
    nv: "bg-nv/15 text-nv-light ring-nv/40",
  } as const;
  return <span className={cx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1", tones[tone], className)}>{children}</span>;
}

export function PolicyChip({ id, active, onHover }: { id: string; active?: boolean; onHover?: (id: string | null) => void }) {
  return (
    <button
      type="button"
      onMouseEnter={() => onHover?.(id)}
      onMouseLeave={() => onHover?.(null)}
      onClick={() => document.getElementById(`policy-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}
      className={cx(
        "rounded border px-1.5 py-px font-mono text-[10px] transition",
        active ? "border-nv bg-nv/20 text-nv-light" : "border-ops-line bg-ops-panel2 text-slate-300 hover:border-nv/60",
      )}
    >
      {id}
    </button>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="flex h-full min-h-[120px] items-center justify-center rounded-lg border border-dashed border-ops-line p-6 text-center text-sm text-ops-muted">{children}</div>;
}
