"use client";

import Link from "next/link";
import { BookOpen, Cpu, Database, RotateCcw, Shield, Sparkles, Waypoints } from "lucide-react";
import type { Runtime } from "@/lib/types";
import { cx } from "@/lib/format";

function RuntimeChip({ icon, label, value, nvidia, title }: { icon: React.ReactNode; label: string; value: string; nvidia: boolean; title: string }) {
  return (
    <div
      title={title}
      className={cx(
        "flex items-center gap-2 rounded-lg border px-2.5 py-1.5",
        nvidia ? "border-nv/40 bg-nv/10" : "border-amber-500/30 bg-amber-500/5",
      )}
    >
      <span className={nvidia ? "text-nv" : "text-amber-300"}>{icon}</span>
      <div className="leading-tight">
        <div className="text-[10px] uppercase tracking-wider text-ops-muted">{label}</div>
        <div className={cx("text-xs font-semibold", nvidia ? "text-nv-light" : "text-amber-200")}>{value}</div>
      </div>
    </div>
  );
}

export function TopBar({ runtime, onReset, resetting }: { runtime: Runtime | null; onReset: () => void; resetting: boolean }) {
  return (
    <header className="sticky top-0 z-30 border-b border-ops-line bg-ops-bg/85 backdrop-blur">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-4 px-5 py-3">
        <Link href="/" className="flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-gradient-to-br from-nv to-emerald-700 shadow-lg shadow-nv/20">
            <Waypoints className="h-5 w-5 text-black" />
          </div>
          <div className="leading-tight">
            <div className="text-lg font-bold tracking-tight">
              Re<span className="text-nv">Route</span>
            </div>
            <div className="text-[11px] text-ops-muted">Autonomous Airline Disruption Recovery Agent</div>
          </div>
        </Link>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          {runtime ? (
            <>
              <RuntimeChip
                icon={<Sparkles className="h-4 w-4" />}
                label="Reasoning"
                value={runtime.llm.nvidia ? runtime.llm.model.replace("nvidia/", "") : "Mock planner (demo)"}
                nvidia={runtime.llm.nvidia}
                title={runtime.llm.reason ?? (runtime.llm.nvidia ? "NVIDIA NIM - Nemotron tool calling" : "scripted planner")}
              />
              <RuntimeChip
                icon={<Database className="h-4 w-4" />}
                label="Policy RAG"
                value={runtime.retriever.nvidia ? "NeMo Retriever" : "BM25 (fallback)"}
                nvidia={runtime.retriever.nvidia}
                title={runtime.retriever.nvidia ? runtime.retriever.models.join(" + ") : "RETRIEVER_PROVIDER=lexical"}
              />
              <RuntimeChip
                icon={<Cpu className="h-4 w-4" />}
                label="Optimizer"
                value={runtime.optimizer.nvidia ? "NVIDIA cuOpt" : "HiGHS CPU (fallback)"}
                nvidia={runtime.optimizer.nvidia}
                title={runtime.optimizer.endpoint ?? "OPTIMIZATION_PROVIDER=fallback"}
              />
              <RuntimeChip
                icon={<Shield className="h-4 w-4" />}
                label="Sandbox"
                value={runtime.security.runtime === "openshell" ? "OpenShell" : "Policy mirror"}
                nvidia={runtime.security.runtime === "openshell"}
                title={runtime.security.enforced_by}
              />
            </>
          ) : (
            <span className="text-xs text-ops-muted">connecting to API…</span>
          )}
          <Link
            href="/guide"
            className="ml-2 flex items-center gap-1.5 rounded-lg border border-ops-line px-3 py-2 text-xs font-semibold text-slate-200 hover:border-nv/60 hover:text-white"
          >
            <BookOpen className="h-4 w-4" /> 심사위원 가이드
          </Link>
          <button
            onClick={onReset}
            disabled={resetting}
            className="flex items-center gap-1.5 rounded-lg border border-ops-line px-3 py-2 text-xs text-ops-muted hover:text-white disabled:opacity-50"
            title="Reset seed data and agent history"
          >
            <RotateCcw className={cx("h-4 w-4", resetting && "animate-spin")} /> Reset
          </button>
        </div>
      </div>
    </header>
  );
}
