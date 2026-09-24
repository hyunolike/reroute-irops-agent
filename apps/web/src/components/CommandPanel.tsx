"use client";

import { useState } from "react";
import { CornerDownLeft, Loader2, Play } from "lucide-react";

export const SCENARIOS = [
  { label: "KE123 결항 · 35명", command: "KE123편이 결항됐어. 영향 승객을 확인하고 최적 재배정안을 만들어줘." },
  { label: "KE125 45분 지연", command: "KE125편 지연됐는데 승객 재배정이 필요한지 확인해줘." },
  { label: "존재하지 않는 편 (ZZ999)", command: "ZZ999편 결항 처리해줘." },
];

export function CommandPanel({ onRun, busy }: { onRun: (cmd: string) => void; busy: boolean }) {
  const [cmd, setCmd] = useState(SCENARIOS[0].command);
  return (
    <div className="rounded-xl border border-ops-line bg-ops-panel p-3">
      <form
        className="flex flex-col gap-2 md:flex-row"
        onSubmit={(e) => {
          e.preventDefault();
          if (cmd.trim()) onRun(cmd.trim());
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
          {busy ? "Agent running…" : "Run Agent"}
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
