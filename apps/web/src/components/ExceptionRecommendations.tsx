"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Check, Lightbulb, Loader2, ShieldCheck, X } from "lucide-react";
import { Panel, Pill, PolicyChip } from "./ui";
import { api } from "@/lib/api";
import { cx } from "@/lib/format";
import type { DecisionKind, ExceptionRecommendation, Plan, ResolutionAction } from "@/lib/types";

const ACTION_LABEL: Record<ResolutionAction, string> = {
  CONFIRM_SOLVER_ASSIGNMENT: "solver 배정 확인",
  REASSIGN_TO_OPTION: "다른 편으로 재배정",
  REQUEST_POLICY_WAIVER: "정책 면제 (duty manager)",
  OFFER_REFUND: "환불 제안",
  REROUTE_OFFLINE: "시스템 외 처리",
};

export function ExceptionRecommendations({
  plan,
  decisions,
  setDecision,
  hoverPolicy,
  setHoverPolicy,
  onLoaded,
}: {
  plan: Plan;
  decisions: Record<string, DecisionKind>;
  setDecision: (pid: string, d: DecisionKind | null) => void;
  hoverPolicy: string | null;
  setHoverPolicy: (id: string | null) => void;
  onLoaded?: (recs: ExceptionRecommendation[]) => void;
}) {
  const [recs, setRecs] = useState<ExceptionRecommendation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pending = plan.approval?.status === "PENDING";
  const decided = Object.fromEntries((plan.approval?.exception_decisions ?? []).map((d) => [d.passenger_id, d]));
  const names = Object.fromEntries(plan.items.map((i) => [i.passenger_id, i.passenger_name]));

  useEffect(() => {
    setRecs(null);
    setError(null);
    api
      .exceptionResolutions(plan.id)
      .then((r) => {
        setRecs(r.recommendations ?? []);
        onLoaded?.(r.recommendations ?? []);
      })
      .catch((e) => setError(String(e)));
    // reload per plan, not per callback identity
  }, [plan.id]);

  return (
    <Panel
      title="Exception Recommendations"
      subtitle="LLM 제안 → 결정적 검증기가 재고·정책으로 재검증 → 운영자가 승인 시 수락/거절"
      icon={<Lightbulb className="h-4 w-4" />}
      right={recs && <Pill tone="violet">{recs.length} passengers</Pill>}
    >
      {error && <div className="text-xs text-rose-300">{error}</div>}
      {!recs && !error && (
        <div className="flex items-center gap-2 text-xs text-ops-muted">
          <Loader2 className="h-4 w-4 animate-spin text-nv" /> 최신 재고로 재검증 중…
        </div>
      )}
      {recs && recs.length === 0 && <div className="text-xs text-ops-muted">예외 승객에 대한 권고가 없습니다. 운영자가 직접 판단합니다.</div>}
      <div className="grid gap-3 md:grid-cols-2">
        {recs?.map((r) => {
          const rejected = r.verdict === "REJECTED";
          const choice = pending ? decisions[r.passenger_id] : decided[r.passenger_id]?.decision;
          return (
            <div
              key={r.passenger_id}
              className={cx(
                "space-y-2 rounded-lg border p-3 text-xs",
                choice === "ACCEPT" ? "border-nv/60 bg-nv/5" : choice === "REJECT" ? "border-rose-500/40 bg-rose-500/5" : "border-ops-line bg-ops-panel2",
              )}
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="font-semibold text-slate-100">
                    {names[r.passenger_id] ?? r.passenger_id} <span className="font-mono text-ops-muted">{r.passenger_id}</span>
                  </div>
                  <div className="mt-0.5 text-slate-200">
                    {ACTION_LABEL[r.action]}
                    {r.seat && (
                      <span className="font-mono text-nv-light">
                        {" "}
                        → {r.seat.flight_no} {r.seat.cabin.toLowerCase()}
                      </span>
                    )}
                  </div>
                </div>
                {rejected ? (
                  <Pill tone="red" className="shrink-0 whitespace-nowrap">
                    <AlertTriangle className="h-3 w-3" /> 검증 실패
                  </Pill>
                ) : r.required_role ? (
                  <Pill tone="violet" className="shrink-0 whitespace-nowrap">
                    <ShieldCheck className="h-3 w-3" /> {r.required_role} 승인 필요
                  </Pill>
                ) : (
                  <Pill tone="green" className="shrink-0 whitespace-nowrap">
                    <ShieldCheck className="h-3 w-3" /> 검증 통과
                  </Pill>
                )}
              </div>
              {r.proposal.rationale && <div className="text-ops-muted">{r.proposal.rationale}</div>}
              {r.proposal.checklist.length > 0 && (
                <ul className="list-inside list-disc text-amber-200/90">
                  {r.proposal.checklist.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              )}
              {r.waived.length > 0 && <div className="text-violet-200/90">면제 대상: {r.waived.join("; ")}</div>}
              {rejected && (
                <div className="rounded border border-rose-500/40 bg-rose-500/10 p-2 font-mono text-[11px] text-rose-200">
                  {r.violations.join(" · ")}
                  {r.planner_verdict !== r.verdict && <div className="mt-1 text-rose-300/80">에이전트 제출 시에는 통과했지만, 최신 재고로 다시 검증하니 실패했습니다.</div>}
                </div>
              )}
              <div className="flex flex-wrap items-center gap-1">
                {r.proposal.policy_ids.map((id) => (
                  <PolicyChip key={id} id={id} active={hoverPolicy === id} onHover={setHoverPolicy} />
                ))}
                <span className="ml-auto font-mono text-[10px] text-ops-muted" title="planner · prompt version">
                  {r.planner} · {r.prompt_version}
                </span>
              </div>
              {pending ? (
                <div className="flex gap-2">
                  <button
                    onClick={() => setDecision(r.passenger_id, choice === "ACCEPT" ? null : "ACCEPT")}
                    disabled={rejected}
                    className={cx(
                      "flex flex-1 items-center justify-center gap-1 rounded-md border py-1.5 font-semibold disabled:cursor-not-allowed disabled:opacity-40",
                      choice === "ACCEPT" ? "border-nv bg-nv text-black" : "border-nv/50 text-nv-light hover:bg-nv/10",
                    )}
                  >
                    <Check className="h-3.5 w-3.5" /> 수락
                  </button>
                  <button
                    onClick={() => setDecision(r.passenger_id, choice === "REJECT" ? null : "REJECT")}
                    className={cx(
                      "flex flex-1 items-center justify-center gap-1 rounded-md border py-1.5 font-semibold",
                      choice === "REJECT" ? "border-rose-500 bg-rose-500/80 text-white" : "border-rose-500/50 text-rose-300 hover:bg-rose-500/10",
                    )}
                  >
                    <X className="h-3.5 w-3.5" /> 거절
                  </button>
                </div>
              ) : (
                choice && (
                  <div className={cx("font-semibold", choice === "REJECT" ? "text-rose-300" : "text-nv-light")}>
                    {choice === "ACCEPT" ? "수락됨" : choice === "REJECT" ? "거절됨" : "운영자 수정"} · {decided[r.passenger_id]?.decided_by}
                  </div>
                )
              )}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}
