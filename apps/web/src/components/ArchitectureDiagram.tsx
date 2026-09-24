import { ArrowDown, Bot, Calculator, Database, Gavel, Globe, Library, Monitor, PlaneTakeoff, ScrollText, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "@/lib/format";

function Box({ icon, title, sub, tone = "slate", className }: { icon: ReactNode; title: string; sub?: string; tone?: "nv" | "slate" | "violet" | "sky"; className?: string }) {
  const tones = {
    nv: "border-nv/50 bg-nv/10",
    slate: "border-ops-line bg-ops-panel2",
    violet: "border-violet-500/40 bg-violet-500/10",
    sky: "border-sky-500/30 bg-sky-500/5",
  };
  return (
    <div className={cx("rounded-lg border px-3 py-2", tones[tone], className)}>
      <div className="flex items-center gap-2 text-sm font-semibold text-white">
        <span className={tone === "nv" ? "text-nv" : tone === "violet" ? "text-violet-300" : "text-slate-300"}>{icon}</span>
        {title}
      </div>
      {sub && <div className="mt-0.5 text-[11px] leading-snug text-ops-muted">{sub}</div>}
    </div>
  );
}

const Down = () => (
  <div className="flex justify-center py-1 text-ops-muted">
    <ArrowDown className="h-4 w-4" />
  </div>
);

export function ArchitectureDiagram() {
  return (
    <div className="rounded-xl border border-ops-line bg-ops-bg/60 p-4">
      <Box icon={<Monitor className="h-4 w-4" />} title="Operator · Next.js Operations Dashboard" sub="명령 입력 · 실시간 Agent 활동(SSE) · 재배정안 검토 · 승인/반려 · 감사 로그" />
      <Down />
      <div className="grid gap-3 md:grid-cols-3">
        <Box icon={<Gavel className="h-4 w-4" />} title="Approval Gateway" sub="PENDING→APPROVED/REJECTED/EXPIRED · 서명된 1회용 토큰 발급" tone="violet" />
        <Box icon={<ScrollText className="h-4 w-4" />} title="Agent API · Event Log · Audit" sub="FastAPI control plane · PostgreSQL" />
        <Box icon={<Database className="h-4 w-4" />} title="PostgreSQL" sub="airline domain + agent domain (분리된 소유권)" />
      </div>
      <Down />
      <div className="rounded-xl border-2 border-dashed border-nv/50 p-3">
        <div className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-nv">
          <ShieldCheck className="h-4 w-4" /> NVIDIA OpenShell sandbox — deny-by-default egress · Landlock FS · non-root
        </div>
        <Box
          icon={<Bot className="h-4 w-4" />}
          title="ReRoute Agent (NemoClaw-style governed runtime)"
          sub="Nemotron via NIM: 목표 해석 · 계획 · Tool 선택 · 예외 설명 — 오케스트레이터가 스키마/전제조건/단계예산/상태머신을 강제"
          tone="nv"
        />
        <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-6">
          {["get_disrupted_flight", "get_affected_passengers", "search_alternative_flights", "search_rebooking_policy", "optimize_rebooking", "propose_rebooking"].map((t) => (
            <code key={t} className="rounded bg-ops-panel2 px-1.5 py-1 text-center text-[10px] text-sky-300">
              {t}
            </code>
          ))}
        </div>
        <code className="mt-2 block rounded border border-violet-500/40 bg-violet-500/10 px-1.5 py-1 text-center text-[10px] text-violet-200">execute_rebooking — Approval Gateway 승인 후에만</code>
      </div>
      <Down />
      <div className="grid gap-3 md:grid-cols-4">
        <Box icon={<PlaneTakeoff className="h-4 w-4" />} title="Mock Airline API" sub="운항·승객·재고·예약 (HTTP) · 토큰 없는 쓰기 거부" />
        <Box icon={<Library className="h-4 w-4" />} title="Knowledge Service" sub="NeMo Retriever embed + rerank NIM · policy provenance" tone="nv" />
        <Box icon={<Calculator className="h-4 w-4" />} title="Optimization Service" sub="MILP formulation → NVIDIA cuOpt server" tone="nv" />
        <Box icon={<Globe className="h-4 w-4" />} title="NVIDIA NIM" sub="integrate.api.nvidia.com /v1/chat/completions (Nemotron)" tone="nv" />
      </div>
    </div>
  );
}
