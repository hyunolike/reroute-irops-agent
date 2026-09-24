export type AgentState =
  | "RECEIVED"
  | "ANALYZING_DISRUPTION"
  | "FETCHING_PASSENGERS"
  | "SEARCHING_ALTERNATIVES"
  | "RETRIEVING_POLICIES"
  | "OPTIMIZING"
  | "GENERATING_PROPOSAL"
  | "WAITING_APPROVAL"
  | "EXECUTING"
  | "COMPLETED"
  | "REJECTED"
  | "FAILED";

export type Component =
  | "orchestrator"
  | "nemotron"
  | "mock-llm"
  | "airline-api"
  | "nemo-retriever"
  | "lexical-retriever"
  | "cuopt"
  | "fallback-solver"
  | "approval-gateway"
  | "openshell"
  | "policy-mirror";

export interface AgentEvent {
  id: number;
  seq: number;
  type: "STATE_CHANGED" | "PLANNER" | "TOOL_CALL" | "TOOL_RESULT" | "TOOL_ERROR" | "GUARDRAIL" | "APPROVAL" | "REPORT";
  state: AgentState;
  component: Component;
  title: string;
  detail: Record<string, any>;
  duration_ms: number | null;
  created_at: string;
}

export interface Runtime {
  demo_mode: boolean;
  llm: { provider: string; model: string; nvidia: boolean };
  retriever: { provider: string; nvidia: boolean; models: string[] };
  optimizer: { provider: string; nvidia: boolean; endpoint: string | null; fallback_enabled: boolean; health?: { ok: boolean } };
  security: { runtime: string; policy_file: string; enforced_by: string };
  approval: { ttl_minutes: number; required_for: string[] };
  tools: { name: string; mutating: boolean }[];
}

export interface Task {
  id: string;
  command: string;
  state: AgentState;
  flight_no: string | null;
  plan_id: string | null;
  runtime: Runtime & { planner_fallback?: string };
  report: Record<string, any> | null;
  error: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface PlanItem {
  id: string;
  passenger_id: string;
  passenger_name: string;
  tier: string;
  vip: boolean;
  reservation_id: string;
  original_flight: string;
  alternative_flight: string | null;
  original_cabin: "ECONOMY" | "BUSINESS";
  new_cabin: "ECONOMY" | "BUSINESS" | null;
  delay_minutes: number | null;
  score: number;
  penalties: Record<string, any>;
  reason: string;
  policy_ids: string[];
  status: "AUTO_ASSIGNED" | "MANUAL_REVIEW" | "NO_FEASIBLE" | "EXECUTED" | "EXECUTION_FAILED" | "HELD_FOR_OPERATOR";
  new_reservation_id: string | null;
}

export interface PolicyHit {
  policy_id: string;
  title: string;
  source_document: string;
  section: string;
  text: string;
  score: number;
  params: Record<string, any>;
  retriever: string;
}

export interface Baseline {
  method: string;
  accommodated: number;
  policy_violations: number;
  missed_connections: number;
  ssr_violations: number;
  business_downgrades: number;
  avg_delay_minutes: number;
  vip_avg_delay_minutes: number;
}

export interface Plan {
  id: string;
  task_id: string;
  flight_no: string;
  status: string;
  solver: string;
  objective_value: number;
  summary: {
    affected: number;
    assigned: number;
    auto_assigned: number;
    manual_review: number;
    no_feasible: number;
    business_downgrades: number;
    connections_protected: number;
    avg_delay_minutes: number;
    vip_avg_delay_minutes: number;
    flight_loads: Record<string, { business_assigned: number; economy_assigned: number; business_available: number; economy_available: number }>;
    provider: string;
    nvidia: boolean;
    status: string;
    solve_time_ms: number;
    variables: number;
    constraints: number;
    excluded_flights: { flight_no: string; reason: string; constraint: string; policy_id: string | null }[];
    notes: string[];
  };
  baseline: Baseline;
  policy_evidence: PolicyHit[];
  explanation: string;
  execution_report: Record<string, any> | null;
  created_at: string;
  approval: {
    id: string;
    status: "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED";
    requested_by: string;
    approved_by: string | null;
    comment: string | null;
    approved_manual_item_ids: string[];
    created_at: string;
    approved_at: string | null;
    expires_at: string;
  } | null;
  items: PlanItem[];
}

export interface Flight {
  flight_no: string;
  carrier: string;
  origin: string;
  destination: string;
  departure_time: string;
  arrival_time: string;
  status: string;
  economy_capacity: number;
  business_capacity: number;
  economy_available: number;
  business_available: number;
  aircraft: string;
  disruption: { type: string; reason: string; delay_minutes: number; airline_fault: boolean; occurred_at: string } | null;
}

export interface AuditEntry {
  id: number;
  timestamp: string;
  agent: string;
  tool: string;
  target: string;
  action: string;
  policy: string;
  result: "ALLOW" | "DENY" | "SUCCESS" | "FAILURE";
  enforced_by: string;
  task_id: string | null;
  details: Record<string, any>;
}

export interface SecurityPolicy {
  runtime: string;
  source: string;
  filesystem: { include_workdir?: boolean; read_only?: string[]; read_write?: string[] };
  process: Record<string, string>;
  network: { policy: string; host: string; port: number; rules: string[]; deny_rules: string[]; enforcement: string }[];
  probes: { id: string; label: string; kind: "network" | "file"; method?: string; url?: string; path?: string; expect: string }[];
}

export interface ProbeResult {
  target: string;
  result: "ALLOW" | "DENY";
  policy: string;
  reason: string;
  enforced_by: string;
}
