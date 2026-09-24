import { NotebookPen } from "lucide-react";
import { Panel, PolicyChip } from "./ui";
import type { ReactNode } from "react";

function inline(text: string, hover: string | null, setHover: (id: string | null) => void): ReactNode[] {
  const parts = text.split(/(\[[A-Z]{2,5}-\d{3}\]|\*\*[^*]+\*\*)/g);
  return parts.map((p, i) => {
    const m = p.match(/^\[([A-Z]{2,5}-\d{3})\]$/);
    if (m)
      return (
        <span key={i} className="mx-0.5 inline-block align-middle">
          <PolicyChip id={m[1]} active={hover === m[1]} onHover={setHover} />
        </span>
      );
    if (p.startsWith("**") && p.endsWith("**")) return <strong key={i} className="text-white">{p.slice(2, -2)}</strong>;
    return <span key={i}>{p}</span>;
  });
}

export function Briefing({ text, author, hover, setHover }: { text: string; author: string; hover: string | null; setHover: (id: string | null) => void }) {
  const lines = text.split("\n");
  return (
    <Panel title="Operator Briefing" subtitle={`written by ${author} · grounded in solver output + cited policies`} icon={<NotebookPen className="h-4 w-4" />}>
      <div className="space-y-1 text-[13px] leading-relaxed text-slate-300">
        {lines.map((l, i) => {
          if (!l.trim()) return <div key={i} className="h-1" />;
          if (/^\*\*.+\*\*$/.test(l.trim()))
            return (
              <h3 key={i} className="pt-1 text-xs font-bold uppercase tracking-wider text-nv-light">
                {l.trim().slice(2, -2)}
              </h3>
            );
          if (l.trim().startsWith("- "))
            return (
              <div key={i} className="flex gap-2 pl-1">
                <span className="text-nv">•</span>
                <span>{inline(l.trim().slice(2), hover, setHover)}</span>
              </div>
            );
          return <p key={i}>{inline(l, hover, setHover)}</p>;
        })}
      </div>
    </Panel>
  );
}
