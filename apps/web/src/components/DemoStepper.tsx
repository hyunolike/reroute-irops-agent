import { Bot, CheckCircle2, Cpu, MessageSquareText, UserCheck } from "lucide-react";
import { cx } from "@/lib/format";
import type { AgentState } from "@/lib/types";

const STEPS = [
  { key: 0, icon: MessageSquareText, title: "① 목표 한 문장", desc: "운영자가 자연어로 지시" },
  { key: 1, icon: Bot, title: "② 자율 계획 · 도구 실행", desc: "Nemotron이 도구를 골라 호출" },
  { key: 2, icon: Cpu, title: "③ 규정 기반 최적화", desc: "RAG 규정 → cuOpt MILP" },
  { key: 3, icon: UserCheck, title: "④ 사람 승인", desc: "승인 전 예약 변경 불가" },
  { key: 4, icon: CheckCircle2, title: "⑤ 실행 · 감사 로그", desc: "Booking API + Audit" },
] as const;

export function stageOf(state: AgentState | undefined): number {
  switch (state) {
    case undefined:
      return 0;
    case "RECEIVED":
    case "ANALYZING_DISRUPTION":
    case "FETCHING_PASSENGERS":
    case "SEARCHING_ALTERNATIVES":
      return 1;
    case "RETRIEVING_POLICIES":
    case "OPTIMIZING":
    case "GENERATING_PROPOSAL":
      return 2;
    case "WAITING_APPROVAL":
      return 3;
    default:
      return 4;
  }
}

export function DemoStepper({ state }: { state?: AgentState }) {
  const stage = stageOf(state);
  const done = state === "COMPLETED" || state === "REJECTED";
  return (
    <ol className="grid grid-cols-2 gap-2 md:grid-cols-5">
      {STEPS.map((s) => {
        const Icon = s.icon;
        const isDone = s.key < stage || (done && s.key === stage);
        const active = s.key === stage && !done;
        return (
          <li
            key={s.key}
            className={cx(
              "flex items-center gap-3 rounded-xl border px-3 py-2.5 transition-all",
              active && "border-nv bg-nv/10 shadow-[0_0_24px_-6px_rgba(118,185,0,0.6)]",
              isDone && "border-nv/30 bg-ops-panel",
              !active && !isDone && "border-ops-line bg-ops-panel/60 opacity-70",
            )}
          >
            <span className={cx("grid h-8 w-8 shrink-0 place-items-center rounded-lg", active ? "bg-nv text-black animate-pulseRing" : isDone ? "bg-nv/20 text-nv" : "bg-ops-panel2 text-ops-muted")}>
              <Icon className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <div className={cx("truncate text-sm font-semibold", active || isDone ? "text-white" : "text-slate-400")}>{s.title}</div>
              <div className="truncate text-[11px] text-ops-muted">{s.desc}</div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
