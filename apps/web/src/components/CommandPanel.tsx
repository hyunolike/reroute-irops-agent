"use client";

import { useState } from "react";
import { Bot, CornerDownLeft, Loader2, Play } from "lucide-react";
import type { AgentChoice, Runtime } from "@/lib/types";

export const SCENARIOS = [
  { label: "KE123 결항 · 35명", command: "KE123편이 결항됐어. 영향 승객을 확인하고 최적 재배정안을 만들어줘." },
  { label: "KE125 45분 지연", command: "KE125편 지연됐는데 승객 재배정이 필요한지 확인해줘." },
  { label: "존재하지 않는 편 (ZZ999)", command: "ZZ999편 결항 처리해줘." },
];

const AGENTS: { id: AgentChoice; label: string; hint: string }[] = [
  { id: "reroute", label: "ReRoute 에이전트", hint: "Nemotron이 계획 · OpenShell 샌드박스에서 실행" },
  { id: "openclaw", label: "OpenClaw (NemoClaw)", hint: "NemoClaw의 OpenClaw가 MCP로 ReRoute 도구를 사용" },
];

export function CommandPanel({
  onRun,
  busy,
  openclaw,
}: {
  onRun: (cmd: string, agent: AgentChoice) => void;
  busy: boolean;
  openclaw?: Runtime["openclaw"];
}) {
  const [cmd, setCmd] = useState(SCENARIOS[0].command);
  const [agent, setAgent] = useState<AgentChoice>("reroute");
  const clawOnline = !!openclaw?.connected;
  return (
    <div className="rounded-xl border border-ops-line bg-ops-panel p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs" role="radiogroup" aria-label="Agent">
        <span className="text-ops-muted">에이전트:</span>
        {AGENTS.map((a) => {
          const disabled = a.id === "openclaw" && !clawOnline;
          const on = agent === a.id;
          return (
            <button
              key={a.id}
              type="button"
              role="radio"
              aria-checked={on}
              disabled={disabled}
              title={disabled ? "OpenClaw 브리지가 연결되지 않았습니다 (NemoClaw 호스트 확인)" : a.hint}
              onClick={() => setAgent(a.id)}
              className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 transition disabled:cursor-not-allowed disabled:opacity-40 ${
                on ? "border-nv bg-nv/15 text-white" : "border-ops-line text-slate-300 hover:border-nv/60 hover:text-white"
              }`}
            >
              <Bot className="h-3.5 w-3.5" />
              {a.label}
              {a.id === "openclaw" && (
                <span className={`h-1.5 w-1.5 rounded-full ${clawOnline ? "bg-nv" : "bg-rose-400"}`} aria-label={clawOnline ? "connected" : "offline"} />
              )}
            </button>
          );
        })}
        {!clawOnline && <span className="text-ops-muted">OpenClaw 연결 안 됨</span>}
      </div>
      <form
        className="flex flex-col gap-2 md:flex-row"
        onSubmit={(e) => {
          e.preventDefault();
          if (cmd.trim()) onRun(cmd.trim(), agent === "openclaw" && !clawOnline ? "reroute" : agent);
        }}
      >
        <div className="relative flex-1">
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 font-mono text-xs text-nv">ops&gt;</span>
          <input
            value={cmd}
            onChange={(e) => setCmd(e.target.value)}
            className="w-full rounded-lg border border-ops-line bg-ops-bg py-3 pl-14 pr-10 text-[15px] text-white outline-none ring-nv/50 placeholder:text-ops-muted focus:border-nv/60 focus:ring-2"
            placeholder="예: KE123편이 결항됐어. 영향 승객을 확인하고 최적 재배정안을 만들어줘."
            aria-label="Agent command"
          />
          <CornerDownLeft className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ops-muted" />
        </div>
        <button
          type="submit"
          disabled={busy}
          className="flex items-center justify-center gap-2 rounded-lg bg-nv px-6 py-3 text-sm font-bold text-black shadow-lg shadow-nv/20 transition hover:bg-nv-light disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
          {busy ? "Agent running…" : agent === "openclaw" ? "Send to OpenClaw" : "Run Agent"}
        </button>
      </form>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        <span className="text-ops-muted">시나리오:</span>
        {SCENARIOS.map((s) => (
          <button
            key={s.label}
            type="button"
            onClick={() => setCmd(s.command)}
            className="rounded-full border border-ops-line px-2.5 py-1 text-slate-300 hover:border-nv/60 hover:text-white"
          >
            {s.label}
          </button>
        ))}
      </div>
    </div>
  );
}
