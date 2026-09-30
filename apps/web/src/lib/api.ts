import type { AgentEvent, AuditEntry, DecisionKind, ExceptionResolutions, Flight, Plan, ProbeResult, Runtime, SecurityPolicy, Task, AgentChoice } from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
export const DEFAULT_OPERATOR = "ops.controller.kim";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!r.ok) {
    let msg = `${r.status}`;
    try {
      const b = await r.json();
      const d = b.detail;
      if (typeof d === "string") msg = d;
      else if (d?.message) {
        // structured refusals, e.g. exception decisions that failed re-verification: message + per-passenger reasons
        const why = Object.entries((d.violations ?? {}) as Record<string, string[]>).map(([pid, v]) => `${pid}: ${v.join("; ")}`);
        msg = [d.message, ...why].join(" · ");
      } else msg = JSON.stringify(d ?? b);
    } catch {}
    throw new Error(msg);
  }
  return r.json();
}

export const api = {
  runtime: () => req<Runtime>("/api/system/runtime"),
  createTask: (command: string, agent: AgentChoice = "reroute") =>
    req<Task>("/api/agent/tasks", { method: "POST", body: JSON.stringify({ command, agent }) }),
  task: (id: string) => req<Task>(`/api/agent/tasks/${id}`),
  tasks: () => req<{ tasks: Task[] }>("/api/agent/tasks"),
  events: (id: string, after = 0) => req<{ events: AgentEvent[] }>(`/api/agent/tasks/${id}/events?stream=false&after=${after}`),
  plan: (id: string) => req<Plan>(`/api/rebooking/plans/${id}`),
  flight: (no: string) => req<Flight>(`/api/flights/${no}`),
  approve: (id: string, operator: string, comment: string, manualIds: string[], decisions: Record<string, DecisionKind> = {}) =>
    req<Plan>(`/api/rebooking/plans/${id}/approve`, {
      method: "POST",
      headers: { "X-Operator-Id": operator },
      body: JSON.stringify({
        comment,
        approved_manual_item_ids: manualIds,
        exception_decisions: Object.entries(decisions).map(([passenger_id, decision]) => ({ passenger_id, decision })),
      }),
    }),
  exceptionResolutions: (id: string) => req<ExceptionResolutions>(`/api/rebooking/plans/${id}/exception-resolutions`),
  reject: (id: string, operator: string, comment: string) =>
    req<Plan>(`/api/rebooking/plans/${id}/reject`, {
      method: "POST",
      headers: { "X-Operator-Id": operator },
      body: JSON.stringify({ comment }),
    }),
  executeWithoutApproval: (id: string) => fetch(`${API_BASE}/api/rebooking/plans/${id}/execute`, { method: "POST" }),
  audit: (limit = 120) => req<{ entries: AuditEntry[] }>(`/api/audit?limit=${limit}`),
  securityPolicy: () => req<SecurityPolicy>("/api/security/policy"),
  probe: (body: Record<string, unknown>) => req<ProbeResult>("/api/security/probes", { method: "POST", body: JSON.stringify(body) }),
  reset: () => req<Record<string, unknown>>("/api/demo/reset", { method: "POST" }),
};
