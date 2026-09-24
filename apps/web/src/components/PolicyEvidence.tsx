import { FileText, Library } from "lucide-react";
import { Panel } from "./ui";
import { cx } from "@/lib/format";
import type { Plan } from "@/lib/types";

export function PolicyEvidence({ plan, hoverPolicy, setHoverPolicy }: { plan: Plan; hoverPolicy: string | null; setHoverPolicy: (id: string | null) => void }) {
  const used = new Set(plan.items.flatMap((i) => i.policy_ids));
  const hits = [...plan.policy_evidence].sort((a, b) => Number(used.has(b.policy_id)) - Number(used.has(a.policy_id)) || a.policy_id.localeCompare(b.policy_id));
  return (
    <Panel
      title="Policy Evidence"
      subtitle={`${hits.length} retrieved policies · ${used.size} cited in allocations · retriever: ${hits[0]?.retriever ?? "—"}`}
      icon={<Library className="h-4 w-4" />}
    >
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {hits.map((h) => (
          <article
            id={`policy-${h.policy_id}`}
            key={h.policy_id}
            onMouseEnter={() => setHoverPolicy(h.policy_id)}
            onMouseLeave={() => setHoverPolicy(null)}
            className={cx(
              "rounded-lg border p-3 transition",
              hoverPolicy === h.policy_id ? "border-nv bg-nv/10" : used.has(h.policy_id) ? "border-ops-line bg-ops-panel2" : "border-ops-line/60 bg-ops-panel2/40 opacity-75",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-mono text-sm font-bold text-nv-light">{h.policy_id}</span>
              <span className="font-mono text-[10px] text-ops-muted" title="relevance score">
                score {h.score.toFixed(2)}
              </span>
            </div>
            <div className="mt-0.5 text-[13px] font-semibold text-slate-100">{h.title}</div>
            <div className="mt-1 flex items-center gap-1 text-[10px] text-ops-muted">
              <FileText className="h-3 w-3" /> documents/{h.source_document}
            </div>
            <p className="mt-2 line-clamp-4 text-[11px] leading-relaxed text-slate-300">{h.text}</p>
            {h.params?.rule && (
              <div className="mt-2 rounded bg-ops-bg px-2 py-1 font-mono text-[10px] text-sky-300">
                → constraint <span className="text-white">{h.params.rule}</span>
                {Object.entries(h.params)
                  .filter(([k]) => !["rule", "airport"].includes(k))
                  .map(([k, v]) => ` ${k}=${JSON.stringify(v)}`)
                  .join("")}
              </div>
            )}
          </article>
        ))}
      </div>
    </Panel>
  );
}
