"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowLeft, CheckCircle2, CircleDashed } from "lucide-react";
import { ArchitectureDiagram } from "@/components/ArchitectureDiagram";
import { Panel, Pill } from "@/components/ui";
import { api } from "@/lib/api";
import type { Runtime } from "@/lib/types";

function Live({ on, yes, no }: { on: boolean | undefined; yes: string; no: string }) {
  if (on === undefined) return <span className="text-ops-muted">—</span>;
  return on ? (
    <span className="flex items-center gap-1 text-nv-light">
      <CheckCircle2 className="h-3.5 w-3.5" /> {yes}
    </span>
  ) : (
    <span className="flex items-center gap-1 text-amber-300">
      <CircleDashed className="h-3.5 w-3.5" /> {no}
    </span>
  );
}

export default function Guide() {
  const [rt, setRt] = useState<Runtime | null>(null);
  useEffect(() => {
    api.runtime().then(setRt).catch(() => {});
  }, []);

  const stack = [
    {
      tech: "Nemotron · NIM",
      role: "목표 해석, 다음 Tool 선택(function calling), 예외 승객 설명 작성. 배정 결정은 하지 않음.",
      where: "apps/api/app/providers/llm/nim.py · agent/orchestrator.py",
      live: <Live on={rt?.llm.nvidia} yes={`live · ${rt?.llm.model}`} no={rt?.llm.reason ?? "scripted planner"} />,
    },
    {
      tech: "NeMo Retriever (RAG)",
      role: "재예약·운임·VIP·MCT·IROPS 규정 검색 (embed → rerank). 결과마다 policy id · 문서 · 점수 제공.",
      where: "apps/api/app/rag/nvidia.py · services/knowledge.py",
      live: <Live on={rt?.retriever.nvidia} yes="live · embed + rerank NIM" no="BM25 fallback (RETRIEVER_PROVIDER=lexical)" />,
    },
    {
      tech: "cuOpt",
      role: "승객×항공편×좌석등급 0-1 MILP를 풀어 최종 배정 계산. Solver 출력이 source of truth.",
      where: "apps/api/app/optimization/{formulation,cuopt}.py",
      live: <Live on={rt?.optimizer.nvidia} yes={`live · ${rt?.optimizer.endpoint}`} no="HiGHS CPU fallback (OPTIMIZATION_PROVIDER=fallback)" />,
    },
    {
      tech: "OpenShell",
      role: "Agent 샌드박스: 허용 API만 egress, 자기 승인 엔드포인트 deny_rule, 파일시스템 allow-list, non-root.",
      where: "nvidia/openshell/policies/reroute-agent.yaml",
      live: <Live on={rt ? rt.security.runtime === "openshell" : undefined} yes="enforced by OpenShell" no="policy mirror (same YAML, in-process)" />,
    },
    {
      tech: "NemoClaw · OpenClaw",
      role: "NemoClaw 샌드박스 안의 OpenClaw가 MCP(/mcp)로 ReRoute 도구를 사용. 같은 가드레일·감사 로그, 승인·실행 도구는 없음(사람만 승인).",
      where: "apps/api/app/integrations/mcp_server.py · nvidia/nemoclaw/ · nvidia/skills/reroute-irops/SKILL.md",
      live: <span className="text-ops-muted">MCP server tested · NemoClaw connection: see nvidia/nemoclaw/README.md</span>,
    },
    {
      tech: "NVIDIA Skills",
      role: "cuopt-numerical-optimization-formulation · cuopt-server-api-python 스킬의 규약(CSR payload, reqId polling)을 따라 구현.",
      where: "nvidia/skills/README.md",
      live: <span className="text-ops-muted">design reference</span>,
    },
  ];

  return (
    <div className="min-h-screen">
      <header className="border-b border-ops-line bg-ops-bg/85">
        <div className="mx-auto flex max-w-[1200px] items-center gap-3 px-5 py-4">
          <Link href="/" className="flex items-center gap-1 text-sm text-ops-muted hover:text-white">
            <ArrowLeft className="h-4 w-4" /> Dashboard
          </Link>
          <span className="text-ops-line">/</span>
          <span className="text-sm font-semibold">심사위원 가이드 (3분)</span>
        </div>
      </header>
      <main className="mx-auto max-w-[1200px] space-y-5 px-5 py-6">
        <section>
          <div className="text-xs font-bold uppercase tracking-[0.2em] text-nv">ReRoute</div>
          <h1 className="mt-1 text-3xl font-bold text-white">Autonomous Airline Disruption Recovery Agent</h1>
          <p className="mt-3 max-w-4xl text-[15px] leading-relaxed text-slate-300">
            “KE123편이 결항됐어. 최적 재배정안을 만들어줘.” — 한 문장을 받은 Agent가 운항·승객·대체편을 조회하고, 항공사 규정을 검색하고, 수리 최적화로 배정을 계산한 뒤,
            운영자 승인을 받아 실제 예약을 변경합니다. <b className="text-white">LLM은 승객 배정을 결정하지 않습니다.</b> Agent는 문제를 정식화하고 규정을 검색하며, 제약이 있는 배정은
            NVIDIA cuOpt에 위임합니다. <b className="text-white">조회성 작업은 자율 실행, 예약 변경은 사람 승인 후에만</b> 실행됩니다.
          </p>
        </section>

        <div className="grid gap-4 md:grid-cols-3">
          <Panel title="Problem">
            <p className="text-sm leading-relaxed text-slate-300">결항 시 수십 명의 승객을 연결편·VIP·특수지원·좌석등급·제휴사 규정을 동시에 만족시키며 제한된 좌석에 빠르게 재배정해야 합니다. 선착순 수작업은 연결편 놓침과 규정 위반을 만듭니다.</p>
          </Panel>
          <Panel title="Why Agentic AI?">
            <p className="text-sm leading-relaxed text-slate-300">
              고정 스크립트가 아니라 상황에 따라 분기합니다: 결항이면 전체 재배정, 45분 지연이면 규정(RBK-002)을 찾아 “조치 불필요”로 종료, 없는 편명은 추측하지 않고 실패 보고.
              필요한 규정이 빠지면 가드레일이 추가 검색을 요구합니다.
            </p>
          </Panel>
          <Panel title="Why NVIDIA?">
            <p className="text-sm leading-relaxed text-slate-300">
              추론(Nemotron/NIM), 근거(NeMo Retriever), 최적화(cuOpt), 통제(OpenShell/NemoClaw)를 한 스택으로 — 각 기술이 “있으면 좋은 것”이 아니라 서로 다른 실패 모드를 막는 역할을
              맡습니다: 환각된 규정, 임의 배정, 과도한 권한.
            </p>
          </Panel>
        </div>

        <Panel title="Architecture">
          <ArchitectureDiagram />
        </Panel>

        <Panel title="NVIDIA stack — role & live status in this deployment" bodyClassName="p-0">
          <table className="w-full text-left text-sm">
            <thead className="text-[11px] uppercase tracking-wider text-ops-muted">
              <tr>
                <th className="px-4 py-2">Technology</th>
                <th className="px-4 py-2">Role in ReRoute</th>
                <th className="px-4 py-2">Code</th>
                <th className="px-4 py-2">This deployment</th>
              </tr>
            </thead>
            <tbody>
              {stack.map((s) => (
                <tr key={s.tech} className="border-t border-ops-line align-top">
                  <td className="px-4 py-3 font-semibold text-white">{s.tech}</td>
                  <td className="px-4 py-3 text-slate-300">{s.role}</td>
                  <td className="px-4 py-3 font-mono text-[11px] text-sky-300">{s.where}</td>
                  <td className="px-4 py-3 text-xs">{s.live}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <div className="grid gap-4 md:grid-cols-2">
          <Panel title="Two boundaries — never confused">
            <div className="space-y-3 text-sm">
              <div className="rounded-lg border border-nv/40 bg-nv/5 p-3">
                <div className="flex items-center gap-2 font-semibold text-white">
                  <Pill tone="nv">Technical</Pill> OpenShell sandbox
                </div>
                <p className="mt-1 text-slate-300">“이 프로세스가 이 엔드포인트에 접근할 수 있는가?” — egress/파일/프로세스 권한. 미등록 호스트, ~/.ssh, 자기 승인 API 호출은 DENY.</p>
              </div>
              <div className="rounded-lg border border-violet-500/40 bg-violet-500/5 p-3">
                <div className="flex items-center gap-2 font-semibold text-white">
                  <Pill tone="violet">Business</Pill> Human approval
                </div>
                <p className="mt-1 text-slate-300">“책임 있는 사람이 이 변경을 승인했는가?” — 승인 시 계획 항목에 묶인 HMAC 서명 토큰(1회용, 5분) 발급. Booking API가 독립 검증 → 승인 후 항목 바꿔치기 불가.</p>
              </div>
            </div>
          </Panel>
          <Panel title="Optimization model (cuOpt MILP)">
            <pre className="whitespace-pre-wrap font-mono text-[12px] leading-relaxed text-slate-300">{`x[p,f,c] ∈ {0,1}   passenger p → flight f, cabin c
y[p]     ∈ {0,1}   passenger p unassigned

C1  Σ_f,c x[p,f,c] + y[p] = 1          one outcome per passenger
C2  Σ_p x[p,f,c] ≤ seats[f,c]           cabin capacity
C3  business kept when possible        downgrade penalty (tier × VIP)
C4  onward_dep − arr(f) ≥ MCT          (MCT-002, from RAG)
C5  dest(f) = dest(original)           (IROP-005 co-terminal)
C6  carrier / window allowed by policy (IROP-002, IROP-003)

min Σ delay·tier + VIP delay + downgrade
    + connection risk + rebooking cost + 100000·y`}</pre>
            <p className="mt-2 text-xs text-ops-muted">가중치: config/optimization.yaml · 제약 파라미터는 검색된 규정의 policy-params에서 컴파일 (검색되지 않으면 보수적 기본값 + 누락 보고).</p>
          </Panel>
        </div>

        <Panel title="2-minute demo script">
          <ol className="list-decimal space-y-1.5 pl-5 text-sm text-slate-300">
            <li>
              Dashboard에서 <b className="text-white">Run Agent</b> — 오른쪽 타임라인에서 Tool 호출과 런타임 배지(Nemotron · NeMo Retriever · cuOpt)를 확인.
            </li>
            <li>KPI: 35명 → 자동 31 · 검토 3 · 대안 없음 1. “Why an optimizer?”에서 선착순 대비 연결편 놓침 4→0, SSR 위반 2→0.</li>
            <li>Allocation 표에서 강도윤 승객의 사유: 7C1102는 연결을 살리지만 IROP-002(무협정 항공사)로 차단 → 운영자 판단 필요. 정책 칩을 hover하면 근거 문서가 강조됨.</li>
            <li>
              <b className="text-white">“승인 없이 execute 호출”</b> 버튼 → HTTP 403 APPROVAL_REQUIRED, 감사 로그에 DENY.
            </li>
            <li>검토 승객 1명 체크 후 Approve → EXECUTING → COMPLETED, Final Report와 실제 좌석 재고 변화.</li>
            <li>
              Security 패널 <b className="text-white">Run all probes</b> → 외부 API · ~/.ssh · 자기 승인 호출 DENY, 허용 API만 ALLOW.
            </li>
            <li>“KE125 45분 지연” 시나리오 → Agent가 RBK-002를 찾아 “재배정 불필요”로 스스로 종료.</li>
          </ol>
        </Panel>

        <Panel title="Real NVIDIA mode vs Demo / Fallback mode" bodyClassName="p-0">
          <table className="w-full text-left text-sm">
            <thead className="text-[11px] uppercase tracking-wider text-ops-muted">
              <tr>
                <th className="px-4 py-2">Component</th>
                <th className="px-4 py-2">Real NVIDIA mode</th>
                <th className="px-4 py-2">Demo / Fallback mode</th>
              </tr>
            </thead>
            <tbody className="text-slate-300">
              {[
                ["Reasoning", "LLM_PROVIDER=auto + NVIDIA_API_KEY → Nemotron via NIM chooses every tool", "no key (or LLM_PROVIDER=mock) → scripted planner (same tools, same guardrails, warning banner)"],
                ["Retrieval", "RETRIEVER_PROVIDER=nvidia → nemotron-3-embed-1b + rerank-vl-1b-v2", "RETRIEVER_PROVIDER=lexical → BM25 over the same documents"],
                ["Optimization", "OPTIMIZATION_PROVIDER=cuopt → cuOpt server (GPU)", "OPTIMIZATION_PROVIDER=fallback → HiGHS (CPU), same MILP"],
                ["Sandbox", "SECURITY_RUNTIME=openshell → agent inside OpenShell", "SECURITY_RUNTIME=policy-mirror → same policy YAML evaluated in-process"],
              ].map((r) => (
                <tr key={r[0]} className="border-t border-ops-line">
                  <td className="px-4 py-2 font-semibold text-white">{r[0]}</td>
                  <td className="px-4 py-2">{r[1]}</td>
                  <td className="px-4 py-2">{r[2]}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="px-4 py-3 text-xs text-ops-muted">UI의 모든 배지는 실제로 동작한 구현을 표시합니다. 폴백이 사용되면 주황색으로 표시되며 NVIDIA 기술로 표기하지 않습니다.</p>
        </Panel>
      </main>
    </div>
  );
}
