"use client";

import { AlertTriangle, Check, Circle, Loader2, ShieldAlert, Sparkles, XCircle } from "lucide-react";
import { ComponentBadge, Panel, Pill } from "./ui";
import { cx } from "@/lib/format";
import type { AgentEvent, AgentState } from "@/lib/types";

const STEPS: { state: AgentState; ko: string; en: string }[] = [
  { state: "RECEIVED", ko: "목표 수신", en: "Goal received" },
  { state: "ANALYZING_DISRUPTION", ko: "결항 분석", en: "Analyzing disruption" },
  { state: "FETCHING_PASSENGERS", ko: "영향 승객 조회", en: "Fetching passengers" },
  { state: "SEARCHING_ALTERNATIVES", ko: "대체편 탐색", en: "Searching alternatives" },
  { state: "RETRIEVING_POLICIES", ko: "규정 검색 (RAG)", en: "Retrieving policies" },
  { state: "OPTIMIZING", ko: "수리 최적화", en: "Optimizing" },
  { state: "GENERATING_PROPOSAL", ko: "재배정안 생성", en: "Generating proposal" },
  { state: "WAITING_APPROVAL", ko: "운영자 승인 대기", en: "Waiting approval" },
  { state: "EXECUTING", ko: "예약 변경 실행", en: "Executing" },
  { state: "COMPLETED", ko: "완료 · 보고", en: "Completed" },
];
const ORDER = STEPS.map((s) => s.state);

type Row =
  | { kind: "tool"; call: AgentEvent; result?: AgentEvent }
  | { kind: "note"; ev: AgentEvent };

function groupRows(events: AgentEvent[]): Map<AgentState, Row[]> {
  const out = new Map<AgentState, Row[]>();
  const push = (s: AgentState, r: Row) => out.set(s, [...(out.get(s) ?? []), r]);
  let pendingNotes: AgentEvent[] = [];
  const open: Row[] = [];
  for (const e of events) {
    if (e.type === "STATE_CHANGED") {
      if (e.state === "FAILED" || e.state === "REJECTED") push(e.state, { kind: "note", ev: e });
      continue;
    }
    if (e.type === "PLANNER") {
      pendingNotes.push(e);
      continue;
    }
    if (e.type === "TOOL_CALL") {
      pendingNotes.forEach((n) => push(e.state, { kind: "note", ev: n }));
      pendingNotes = [];
      const row: Row = { kind: "tool", call: e };
      open.push(row);
      push(e.state, row);
      continue;
    }
    if (e.type === "TOOL_RESULT" || e.type === "TOOL_ERROR") {
      const row = open.find((r) => r.kind === "tool" && !r.result && r.call.detail.tool === e.detail.tool) as
        | { kind: "tool"; call: AgentEvent; result?: AgentEvent }
        | undefined;
      if (row) row.result = e;
      else push(e.state, { kind: "note", ev: e });
      continue;
    }
    pendingNotes.forEach((n) => push(n.state, { kind: "note", ev: n }));
    pendingNotes = [];
    push(e.state, { kind: "note", ev: e });
  }
  pendingNotes.forEach((n) => push(n.state, { kind: "note", ev: n }));
  return out;
}

export function AgentTimeline({ events, state }: { events: AgentEvent[]; state?: AgentState }) {
  const rows = groupRows(events);
  const reached = new Set(events.filter((e) => e.type === "STATE_CHANGED").map((e) => e.state));
  const currentIdx = state ? ORDER.indexOf(state) : -1;
  const failed = state === "FAILED";
  const rejected = state === "REJECTED";
  const noAction = state === "COMPLETED" && !reached.has("WAITING_APPROVAL");
  const toolCalls = events.filter((e) => e.type === "TOOL_CALL").length;

  return (
    <Panel
      title="Agent Activity"
      subtitle="Nemotron plans → tools execute → every step is logged"
      icon={<Sparkles className="h-4 w-4" />}
      right={<span className="font-mono text-xs text-ops-muted">{toolCalls} tool calls</span>}
      bodyClassName="p-0"
    >
      {!state ? (
        <div className="p-6 text-sm text-ops-muted">Run Agent을 누르면 에이전트가 스스로 계획하고 도구를 호출하는 과정이 실시간으로 표시됩니다.</div>
      ) : (
        <ol className="max-h-[760px] overflow-y-auto px-4 py-3">
          {STEPS.map((s, i) => {
            const stepRows = rows.get(s.state) ?? [];
            const isReached = reached.has(s.state);
            const active = state === s.state && !["COMPLETED"].includes(s.state);
            const done = isReached && (i < currentIdx || state === "COMPLETED");
            const skipped = !isReached && (state === "COMPLETED" || rejected || failed);
            if (noAction && skipped) return null;
            return (
              <li key={s.state} className="relative pb-3 pl-8 last:pb-0">
                {i < STEPS.length - 1 && <span className="absolute left-[11px] top-6 h-full w-px bg-ops-line" />}
                <span
                  className={cx(
                    "absolute left-0 top-0.5 grid h-6 w-6 place-items-center rounded-full border",
                    done && "border-nv bg-nv text-black",
                    active && "border-nv bg-ops-bg text-nv animate-pulseRing",
                    !done && !active && "border-ops-line bg-ops-bg text-ops-muted",
                  )}
                >
                  {done ? <Check className="h-3.5 w-3.5" strokeWidth={3} /> : active ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Circle className="h-2.5 w-2.5" />}
                </span>
                <div className={cx("text-sm font-semibold", done || active ? "text-white" : skipped ? "text-slate-600 line-through" : "text-slate-500")}>
                  {s.ko} <span className="ml-1 text-[11px] font-normal text-ops-muted">{s.en}</span>
                  {s.state === "WAITING_APPROVAL" && active && <Pill tone="violet" className="ml-2">human-in-the-loop</Pill>}
                </div>
                {stepRows.length > 0 && <div className="mt-1.5 space-y-1">{stepRows.map((r, k) => <RowView key={k} row={r} compact={s.state === "RETRIEVING_POLICIES"} />)}</div>}
              </li>
            );
          })}
          {(failed || rejected) && (
            <li className="relative pl-8">
              <span className="absolute left-0 top-0.5 grid h-6 w-6 place-items-center rounded-full border border-rose-500 bg-rose-500/20 text-rose-300">
                <XCircle className="h-3.5 w-3.5" />
              </span>
              <div className="text-sm font-semibold text-rose-300">{failed ? "실패 (FAILED)" : "반려 (REJECTED)"}</div>
              {[...(rows.get("FAILED") ?? []), ...(rows.get("REJECTED") ?? [])].map((r, k) => (
                <RowView key={k} row={r} />
              ))}
            </li>
          )}
        </ol>
      )}
    </Panel>
  );
}

function RowView({ row, compact }: { row: Row; compact?: boolean }) {
  if (row.kind === "note") {
    const e = row.ev;
    if (e.type === "PLANNER")
      return (
        <div className="animate-slideIn flex items-start gap-2 rounded-md bg-ops-panel2/60 px-2 py-1.5 text-xs italic text-slate-300">
          <ComponentBadge c={e.component} className="not-italic" />
          <span>“{e.title}”</span>
        </div>
      );
    const warn = e.type === "GUARDRAIL" || e.type === "TOOL_ERROR";
    return (
      <div
        className={cx(
          "animate-slideIn flex items-start gap-2 rounded-md px-2 py-1.5 text-xs",
          warn ? "border border-amber-500/30 bg-amber-500/5 text-amber-200" : e.type === "APPROVAL" ? "border border-violet-500/30 bg-violet-500/5 text-violet-200" : "bg-ops-panel2 text-slate-200",
        )}
      >
        {warn ? <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" /> : e.type === "APPROVAL" ? <ShieldAlert className="mt-px h-3.5 w-3.5 shrink-0" /> : null}
        <ComponentBadge c={e.component} />
        <span>{e.title}</span>
      </div>
    );
  }
  const { call, result } = row;
  const err = result?.type === "TOOL_ERROR";
  const tool = call.detail.tool as string;
  if (compact) {
    const hits = (result?.detail.hits ?? []) as { policy_id: string }[];
    return (
      <div className="animate-slideIn flex flex-wrap items-center gap-1.5 text-[11px] text-slate-300">
        <ComponentBadge c={result?.component ?? call.component} />
        <span className="truncate text-ops-muted">“{call.detail.args?.query}”</span>
        {!result && <Loader2 className="h-3 w-3 animate-spin text-nv" />}
        {hits.slice(0, 2).map((h) => (
          <span key={h.policy_id} className="rounded bg-ops-panel2 px-1 font-mono text-[10px] text-nv-light">
            {h.policy_id}
          </span>
        ))}
      </div>
    );
  }
  return (
    <div className={cx("animate-slideIn rounded-md border px-2 py-1.5", err ? "border-rose-500/30 bg-rose-500/5" : "border-ops-line bg-ops-panel2/70")}>
      <div className="flex items-center gap-2">
        <ComponentBadge c={result?.component ?? call.component} />
        <code className="truncate font-mono text-[11px] text-sky-300">{tool}()</code>
        <span className="ml-auto shrink-0 font-mono text-[10px] text-ops-muted">
          {result ? `${Math.round(result.duration_ms ?? 0)} ms` : <Loader2 className="h-3 w-3 animate-spin text-nv" />}
        </span>
      </div>
      {result && <div className={cx("mt-1 text-xs", err ? "text-rose-200" : "text-slate-200")}>{err ? "✕ " : "✓ "}{result.title}</div>}
    </div>
  );
}
