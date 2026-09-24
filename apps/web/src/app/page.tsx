"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { TopBar } from "@/components/TopBar";
import { DemoStepper } from "@/components/DemoStepper";
import { CommandPanel } from "@/components/CommandPanel";
import { DisruptionCard } from "@/components/DisruptionCard";
import { AgentTimeline } from "@/components/AgentTimeline";
import { BaselineComparison, FlightLoads, Kpis } from "@/components/RecoverySummary";
import { AllocationTable } from "@/components/AllocationTable";
import { PolicyEvidence } from "@/components/PolicyEvidence";
import { Briefing } from "@/components/Briefing";
import { ApprovalPanel } from "@/components/ApprovalPanel";
import { FinalReport } from "@/components/FinalReport";
import { SecurityPanel } from "@/components/SecurityPanel";
import { AuditLog } from "@/components/AuditLog";
import { WelcomeBoard } from "@/components/WelcomeBoard";
import { Empty } from "@/components/ui";
import { api } from "@/lib/api";
import { useAgentTask } from "@/lib/useAgentTask";
import type { Runtime } from "@/lib/types";

const RUNNING = new Set(["RECEIVED", "ANALYZING_DISRUPTION", "FETCHING_PASSENGERS", "SEARCHING_ALTERNATIVES", "RETRIEVING_POLICIES", "OPTIMIZING", "GENERATING_PROPOSAL", "EXECUTING"]);

export default function Dashboard() {
  const [runtime, setRuntime] = useState<Runtime | null>(null);
  const [resetting, setResetting] = useState(false);
  const [hoverPolicy, setHoverPolicy] = useState<string | null>(null);
  const [selectedManual, setSelectedManual] = useState<string[]>([]);
  const [auditKey, setAuditKey] = useState(0);
  const { task, events, plan, error, transport, run, follow, clear, setPlan } = useAgentTask();

  useEffect(() => {
    api.runtime().then(setRuntime).catch(() => setRuntime(null));
  }, []);

  const onRun = useCallback(
    (cmd: string) => {
      setSelectedManual([]);
      void run(cmd);
    },
    [run],
  );

  const onReset = async () => {
    setResetting(true);
    try {
      await api.reset();
      clear();
      setSelectedManual([]);
      setAuditKey((k) => k + 1);
    } finally {
      setResetting(false);
    }
  };

  const busy = !!task && RUNNING.has(task.state);
  const briefingAuthor = events.some((e) => e.component === "nemotron" && e.title.startsWith("Operator briefing")) ? "Nemotron (NIM)" : "grounded template";

  return (
    <div className="min-h-screen">
      <TopBar runtime={runtime} onReset={onReset} resetting={resetting} />
      <main className="mx-auto max-w-[1600px] space-y-4 px-5 py-5">
        {runtime && !runtime.llm.nvidia && (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-xs text-amber-100">
            <b className="text-amber-300">스크립트 플래너로 실행 중</b>
            <span>— 에이전트 틀·도구·가드레일은 동일하지만, 다음 도구 선택은 Nemotron이 아닌 정해진 순서로 이뤄집니다.</span>
            <span className="text-amber-200/80">{runtime.llm.reason}</span>
          </div>
        )}
        <DemoStepper state={task?.state} />
        <CommandPanel onRun={onRun} busy={busy} />
        {error && <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-2 text-sm text-rose-200">API error: {error}</div>}

        <div className="grid gap-4 lg:grid-cols-12">
          <aside className="space-y-4 lg:col-span-4">
            <DisruptionCard events={events} />
            <AgentTimeline events={events} state={task?.state} />
            {task && (
              <div className="text-right font-mono text-[10px] text-ops-muted">
                task {task.id} · stream: {transport ?? "—"}
                {task.runtime?.planner_fallback && <span className="text-amber-300"> · planner fallback active</span>}
              </div>
            )}
          </aside>

          <section className="space-y-4 lg:col-span-8">
            {!task && <WelcomeBoard />}
            {task && !plan && task.report?.outcome !== "NO_ACTION_REQUIRED" && task.state !== "FAILED" && (
              <Empty>
                <Loader2 className="mr-2 h-4 w-4 animate-spin text-nv" /> 에이전트가 데이터를 수집하고 규정을 검색하는 중입니다. 재배정안은 최적화가 끝나면 여기에 표시됩니다.
              </Empty>
            )}
            {task?.state === "FAILED" && (
              <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-200">
                <b>작업 실패:</b> {task.error}
                <div className="mt-1 text-xs text-rose-300/80">에이전트는 사실을 확인할 수 없으면 추측하지 않고 실패를 명확히 보고합니다.</div>
              </div>
            )}
            {task && !plan && task.report && <FinalReport task={task} />}

            {plan && (
              <>
                <Kpis plan={plan} />
                <div className="grid gap-4 xl:grid-cols-2">
                  <Briefing text={plan.explanation} author={briefingAuthor} hover={hoverPolicy} setHover={setHoverPolicy} />
                  <div className="space-y-4">
                    <ApprovalPanel
                      plan={plan}
                      selectedManual={selectedManual}
                      onDecided={(p) => {
                        setPlan(p);
                        setAuditKey((k) => k + 1);
                        void follow();
                      }}
                    />
                    {task?.report && <FinalReport task={task} />}
                  </div>
                </div>
                <div className="grid gap-4 xl:grid-cols-2">
                  <BaselineComparison plan={plan} />
                  <FlightLoads plan={plan} />
                </div>
                <AllocationTable
                  plan={plan}
                  hoverPolicy={hoverPolicy}
                  setHoverPolicy={setHoverPolicy}
                  selectedManual={selectedManual}
                  toggleManual={(id) => setSelectedManual((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))}
                  approvalOpen={plan.approval?.status === "PENDING"}
                />
                <PolicyEvidence plan={plan} hoverPolicy={hoverPolicy} setHoverPolicy={setHoverPolicy} />
              </>
            )}
          </section>
        </div>

        <div className="grid gap-4 xl:grid-cols-2">
          <SecurityPanel onProbe={() => setAuditKey((k) => k + 1)} />
          <AuditLog refreshKey={auditKey} />
        </div>

        <footer className="pb-8 pt-2 text-center text-[11px] leading-relaxed text-ops-muted">
          상단 런타임 칩이 <span className="text-nv-light">초록색</span>이면 실제 NVIDIA 서비스(NIM · NeMo Retriever · cuOpt · OpenShell)가 사용 중이고,{" "}
          <span className="text-amber-300">주황색</span>이면 데모/폴백 구현입니다. ReRoute는 실제로 사용하지 않은 NVIDIA 기술을 사용했다고 표시하지 않습니다.
        </footer>
      </main>
    </div>
  );
}
