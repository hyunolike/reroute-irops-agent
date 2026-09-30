"use client";

import { useEffect, useState } from "react";
import { Ban, CheckCircle2, Clock, Gavel, Loader2, ShieldX, XCircle } from "lucide-react";
import { Panel, Pill } from "./ui";
import { api, DEFAULT_OPERATOR } from "@/lib/api";
import { clock, cx } from "@/lib/format";
import type { DecisionKind, Plan } from "@/lib/types";

function useCountdown(iso?: string) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  if (!iso) return null;
  const ms = new Date(iso).getTime() - now;
  if (ms <= 0) return "expired";
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function ApprovalPanel({
  plan,
  selectedManual,
  decisions = {},
  waiverSelected = false,
  onDecided,
}: {
  plan: Plan;
  selectedManual: string[];
  decisions?: Record<string, DecisionKind>;
  waiverSelected?: boolean;
  onDecided: (p: Plan) => void;
}) {
  const [operator, setOperator] = useState(DEFAULT_OPERATOR);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | "probe" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [probe, setProbe] = useState<string | null>(null);
  const a = plan.approval;
  const countdown = useCountdown(a?.status === "PENDING" ? a.expires_at : undefined);
  const pending = a?.status === "PENDING";
  const autoCount = plan.items.filter((i) => i.status === "AUTO_ASSIGNED").length;
  // checked manual-review rows plus accepted exception recommendations; an explicit decision wins over the checkbox
  const reviewedCount = plan.items.filter((i) =>
    decisions[i.passenger_id] ? decisions[i.passenger_id] === "ACCEPT" : selectedManual.includes(i.id),
  ).length;

  const act = async (kind: "approve" | "reject") => {
    setBusy(kind);
    setError(null);
    try {
      const p = kind === "approve" ? await api.approve(plan.id, operator, comment, selectedManual, decisions) : await api.reject(plan.id, operator, comment || "rejected by operator");
      onDecided(p);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const tryBypass = async () => {
    setBusy("probe");
    const r = await api.executeWithoutApproval(plan.id);
    const body = await r.json().catch(() => ({}));
    setProbe(`HTTP ${r.status} — ${typeof body.detail === "object" ? body.detail.code : body.detail}`);
    setBusy(null);
  };

  return (
    <Panel
      title="Human Approval"
      subtitle="Business authorization boundary — enforced by the backend, not the UI"
      icon={<Gavel className="h-4 w-4" />}
      right={a && <Pill tone={a.status === "APPROVED" ? "green" : a.status === "PENDING" ? "violet" : "red"}>{a.status}</Pill>}
      className={cx(pending && "border-violet-500/50 shadow-[0_0_30px_-10px_rgba(139,92,246,0.6)]")}
    >
      {!a ? null : pending ? (
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-2 text-center text-xs">
            <div className="rounded-lg bg-ops-panel2 p-2">
              <div className="text-xl font-bold text-nv-light">{autoCount}</div>
              <div className="text-ops-muted">auto rebook</div>
            </div>
            <div className="rounded-lg bg-ops-panel2 p-2">
              <div className="text-xl font-bold text-amber-300">+{reviewedCount}</div>
              <div className="text-ops-muted">reviewed items</div>
            </div>
            <div className="rounded-lg bg-ops-panel2 p-2">
              <div className="flex items-center justify-center gap-1 font-mono text-xl font-bold text-slate-200">
                <Clock className="h-4 w-4 text-ops-muted" />
                {countdown}
              </div>
              <div className="text-ops-muted">expires</div>
            </div>
          </div>
          <div className="text-[11px] text-ops-muted">
            requested by <span className="font-mono text-slate-300">{a.requested_by}</span> at {clock(a.created_at)} · manual-review 승객은 표에서 체크한 경우에만 실행됩니다.
          </div>
          <div className="grid grid-cols-2 gap-2">
            <input value={operator} onChange={(e) => setOperator(e.target.value)} className="rounded-lg border border-ops-line bg-ops-bg px-3 py-2 text-xs outline-none focus:border-nv/60" placeholder="operator id" aria-label="operator id" />
            <input value={comment} onChange={(e) => setComment(e.target.value)} className="rounded-lg border border-ops-line bg-ops-bg px-3 py-2 text-xs outline-none focus:border-nv/60" placeholder="comment (optional)" aria-label="comment" />
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => act("approve")}
              disabled={!!busy}
              className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-nv py-3 text-sm font-bold text-black shadow-lg shadow-nv/20 hover:bg-nv-light disabled:opacity-60"
            >
              {busy === "approve" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />} Approve Plan
            </button>
            <button
              onClick={() => act("reject")}
              disabled={!!busy}
              className="flex items-center justify-center gap-2 rounded-lg border border-rose-500/50 px-4 py-3 text-sm font-semibold text-rose-300 hover:bg-rose-500/10 disabled:opacity-60"
            >
              <XCircle className="h-4 w-4" /> Reject
            </button>
          </div>
          <button
            onClick={tryBypass}
            disabled={!!busy}
            className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-rose-500/40 py-2 text-[11px] text-rose-300/90 hover:bg-rose-500/5"
          >
            <ShieldX className="h-3.5 w-3.5" /> 보안 데모: 승인 없이 execute API 직접 호출해보기
          </button>
          {probe && (
            <div className="flex items-center gap-2 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 font-mono text-xs text-rose-200">
              <Ban className="h-4 w-4" /> {probe} → blocked & audited
            </div>
          )}
          {Object.keys(decisions).length > 0 && (
            <div className="text-[11px] text-ops-muted">
              예외 권고 결정 {Object.keys(decisions).length}건 포함 (수락 {Object.values(decisions).filter((d) => d === "ACCEPT").length}) — 승인 시 control plane이 최신 재고로 다시 검증합니다.
            </div>
          )}
          {waiverSelected && (
            <div className="rounded-lg border border-violet-500/40 bg-violet-500/10 px-3 py-2 text-[11px] text-violet-200">
              정책 면제가 포함되어 있습니다. duty manager 권한이 있는 operator id로 승인해야 합니다 (데모: <span className="font-mono">dm.park</span>).
            </div>
          )}
          {error && <div className="text-xs text-rose-300">{error}</div>}
        </div>
      ) : (
        <div className="space-y-1 text-sm">
          <div className="text-slate-200">
            {a.status === "APPROVED" ? "승인" : a.status === "REJECTED" ? "반려" : "만료"} by <span className="font-mono text-white">{a.approved_by ?? "—"}</span>
            {a.approved_at && <span className="text-ops-muted"> · {clock(a.approved_at)}</span>}
          </div>
          {a.comment && <div className="text-xs text-ops-muted">“{a.comment}”</div>}
          {a.status === "APPROVED" && a.approved_manual_item_ids.length > 0 && <div className="text-xs text-amber-200">+ {a.approved_manual_item_ids.length} manual-review passengers included</div>}
          {a.status === "APPROVED" && (a.exception_decisions ?? []).length > 0 && (
            <div className="text-xs text-violet-200">
              + exception decisions: {a.exception_decisions.map((d) => `${d.passenger_id} ${d.action ?? d.decision}`).join(", ")}
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}
