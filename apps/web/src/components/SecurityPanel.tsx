"use client";

import { useEffect, useState } from "react";
import { FileLock2, Globe, Loader2, PlayCircle, ShieldCheck } from "lucide-react";
import { Panel, Pill } from "./ui";
import { api } from "@/lib/api";
import { cx } from "@/lib/format";
import type { ProbeResult, SecurityPolicy } from "@/lib/types";

export function SecurityPanel({ onProbe }: { onProbe: () => void }) {
  const [policy, setPolicy] = useState<SecurityPolicy | null>(null);
  const [results, setResults] = useState<Record<string, ProbeResult>>({});
  const [running, setRunning] = useState<string | null>(null);

  useEffect(() => {
    api.securityPolicy().then(setPolicy).catch(() => {});
  }, []);

  const run = async (id: string) => {
    const p = policy?.probes.find((x) => x.id === id);
    if (!p) return;
    setRunning(id);
    try {
      const r = await api.probe({ kind: p.kind, method: p.method, url: p.url, path: p.path });
      setResults((prev) => ({ ...prev, [id]: r }));
      onProbe();
    } finally {
      setRunning(null);
    }
  };
  const runAll = async () => {
    for (const p of policy?.probes ?? []) {
      await run(p.id);
      await new Promise((r) => setTimeout(r, 250));
    }
  };

  const mirror = policy?.runtime !== "openshell";
  return (
    <Panel
      title="Agent Sandbox · OpenShell Policy"
      subtitle="Technical security boundary — deny-by-default egress, filesystem allow-list, non-root process"
      icon={<ShieldCheck className="h-4 w-4" />}
      right={
        <button onClick={runAll} disabled={!!running} className="flex items-center gap-1.5 rounded-lg bg-nv px-3 py-1.5 text-xs font-bold text-black hover:bg-nv-light disabled:opacity-60">
          <PlayCircle className="h-4 w-4" /> Run all probes
        </button>
      }
    >
      {policy && (
        <div className={cx("mb-3 rounded-lg border px-3 py-2 text-[11px]", mirror ? "border-sky-500/30 bg-sky-500/5 text-sky-200" : "border-nv/40 bg-nv/10 text-nv-light")}>
          {mirror ? (
            <>
              <b>Policy mirror mode:</b> 아래 판정은 OpenShell 정책 파일 <code>{policy.source.split("/").slice(-4).join("/")}</code>을 ReRoute가 동일한 의미로
              평가한 결과입니다. 실제 커널/프록시 수준 강제는 <code>make sandbox</code>로 OpenShell 샌드박스에서 실행할 때 적용됩니다.
            </>
          ) : (
            <>
              <b>OpenShell mode:</b> 에이전트가 NVIDIA OpenShell 샌드박스 안에서 실행 중이며, 네트워크/파일시스템 정책은 OpenShell이 강제합니다.
            </>
          )}
        </div>
      )}
      <div className="grid gap-2 md:grid-cols-2">
        {policy?.probes.map((p) => {
          const r = results[p.id];
          return (
            <button
              key={p.id}
              onClick={() => run(p.id)}
              className={cx(
                "group flex items-start gap-2 rounded-lg border p-2.5 text-left transition",
                !r && "border-ops-line bg-ops-panel2 hover:border-nv/50",
                r?.result === "ALLOW" && "border-emerald-500/40 bg-emerald-500/5",
                r?.result === "DENY" && "border-rose-500/50 bg-rose-500/10",
              )}
            >
              <span className="mt-0.5 text-ops-muted">{p.kind === "network" ? <Globe className="h-4 w-4" /> : <FileLock2 className="h-4 w-4" />}</span>
              <div className="min-w-0 flex-1">
                <div className="truncate font-mono text-[11px] text-slate-100">{p.label}</div>
                {r ? (
                  <div className="mt-1 text-[10px] text-ops-muted">
                    policy <span className="font-mono text-slate-300">{r.policy}</span> · {r.reason}
                  </div>
                ) : (
                  <div className="mt-1 text-[10px] text-ops-muted">expected {p.expect}</div>
                )}
              </div>
              {running === p.id ? (
                <Loader2 className="h-4 w-4 animate-spin text-nv" />
              ) : r ? (
                <Pill tone={r.result === "ALLOW" ? "green" : "red"}>{r.result}</Pill>
              ) : null}
            </button>
          );
        })}
      </div>
      {policy && (
        <details className="mt-3 text-[11px] text-ops-muted">
          <summary className="cursor-pointer select-none text-slate-300">Network allow-list ({policy.network.length} endpoints) · filesystem · process</summary>
          <table className="mt-2 w-full">
            <tbody>
              {policy.network.map((n) => (
                <tr key={n.host} className="border-t border-ops-line/60 align-top">
                  <td className="py-1 pr-2 font-mono text-slate-200">
                    {n.host}:{n.port}
                  </td>
                  <td className="py-1 font-mono">
                    {n.rules.map((r) => (
                      <div key={r} className="text-emerald-300/90">
                        allow {r}
                      </div>
                    ))}
                    {n.deny_rules.map((r) => (
                      <div key={r} className="text-rose-300/90">
                        deny {r}
                      </div>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-2 font-mono">
            read_only: {policy.filesystem.read_only?.join(" ")}
            <br />
            read_write: {policy.filesystem.read_write?.join(" ")}
            <br />
            process: run_as_user={policy.process.run_as_user}
          </div>
        </details>
      )}
    </Panel>
  );
}
