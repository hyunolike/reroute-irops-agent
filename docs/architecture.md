# ReRoute Architecture

ReRoute separates **reasoning** (Nemotron), **grounding** (NeMo Retriever), **decision** (cuOpt),
**technical governance** (OpenShell) and **business authorization** (human approval) into distinct
components with explicit interfaces. Each one closes a different failure mode of LLM agents:

| Failure mode | Closed by |
|---|---|
| Hallucinated airline rules | Policies only enter the model through `search_rebooking_policy`; retrieved `policy-params` compile into constraints; missing coverage is reported, not guessed |
| LLM "decides" who flies | Allocation is a MILP solved by cuOpt; the LLM receives only a summary and cannot write the plan |
| Over-privileged agent | OpenShell sandbox: deny-by-default egress, L7 rules, `deny_rules` on approval endpoints, FS allow-list, non-root |
| Unauthorised state change | Approval Gateway + signed, single-use, item-bound token verified by the Booking API |
| Invisible behaviour | Every state change, tool call, policy decision, approval and booking write is in the event log / audit log |

## 1. System architecture

```mermaid
flowchart TB
    op([Operations controller]) --> web[Next.js Operations Dashboard]
    web -- "REST + SSE /api/*" --> cp

    subgraph cp[ReRoute control plane - FastAPI]
        agentapi[Agent API<br/>tasks, events SSE]
        gw[Approval Gateway<br/>PENDING/APPROVED/REJECTED/EXPIRED]
        audit[Audit log]
        ks[Knowledge service<br/>policy RAG]
        os_[Optimization service<br/>MILP formulation]
        internal[Internal worker API<br/>/internal/agent/*]
    end

    subgraph sandbox[NVIDIA OpenShell sandbox]
        agent[ReRoute agent runtime<br/>orchestrator + tools]
    end

    agentapi -. inline mode .-> agent
    internal <-. remote mode: claim / events / plans .-> agent

    agent -- "chat/completions + tools" --> nim[NVIDIA NIM<br/>Nemotron]
    agent -- "GET /api/flights/** · POST bookings+token" --> airline[Mock Airline API<br/>flights · manifest · inventory · booking]
    agent -- "GET /api/policies/search" --> ks
    agent -- "POST /api/optimization/rebooking" --> os_
    ks -- "embed + rerank" --> ret[NeMo Retriever NIMs]
    os_ -- "POST /cuopt/request · GET /cuopt/solution" --> cuopt[NVIDIA cuOpt server - GPU]
    docs[(documents/*.md<br/>airline policies)] --> ks

    cp --> db[(PostgreSQL)]
    airline --> db
```

Bounded contexts share one PostgreSQL instance but not tables: the **airline domain**
(flights, passengers, reservations, disruptions) is owned by the airline service; the **agent domain**
(tasks, events, plans, approvals, audit) by the control plane. The agent never touches either directly.

## 2. Agent workflow (state machine)

```mermaid
stateDiagram-v2
    [*] --> RECEIVED
    RECEIVED --> ANALYZING_DISRUPTION: get_disrupted_flight
    ANALYZING_DISRUPTION --> COMPLETED: operating normally / delay below RBK-002 threshold
    ANALYZING_DISRUPTION --> FETCHING_PASSENGERS: get_affected_passengers
    ANALYZING_DISRUPTION --> FAILED: flight cannot be verified
    FETCHING_PASSENGERS --> SEARCHING_ALTERNATIVES: search_alternative_flights
    SEARCHING_ALTERNATIVES --> RETRIEVING_POLICIES: search_rebooking_policy (parallel queries)
    RETRIEVING_POLICIES --> RETRIEVING_POLICIES: guardrail - coverage incomplete
    RETRIEVING_POLICIES --> OPTIMIZING: optimize_rebooking
    OPTIMIZING --> GENERATING_PROPOSAL: propose_rebooking
    GENERATING_PROPOSAL --> WAITING_APPROVAL
    WAITING_APPROVAL --> EXECUTING: operator approves
    WAITING_APPROVAL --> REJECTED: operator rejects
    WAITING_APPROVAL --> WAITING_APPROVAL: approval expires -> re-approval required
    EXECUTING --> COMPLETED: execute_rebooking + report
    EXECUTING --> FAILED
    COMPLETED --> [*]
    REJECTED --> [*]
    FAILED --> [*]
```

Nemotron chooses the next tool; the orchestrator enforces: known tool, Pydantic-validated arguments,
tool preconditions (e.g. `optimize_rebooking` requires passengers, alternatives and full policy coverage),
a step budget (14), and that `execute_rebooking` only succeeds through the Approval Gateway.
If NIM is unavailable, the task switches to the deterministic planner and says so in the event log.

## 3. Sequence (KE123 cancellation)

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operator
    participant UI as Dashboard
    participant API as Control plane
    participant AG as Agent (sandbox)
    participant LLM as Nemotron (NIM)
    participant AL as Airline API
    participant KS as Knowledge (NeMo Retriever)
    participant SOLVE as Optimization (cuOpt)
    participant GW as Approval Gateway

    Op->>UI: "KE123편이 결항됐어. 최적 재배정안을 만들어줘."
    UI->>API: POST /api/agent/tasks
    API-->>UI: 202 + SSE /events
    API->>AG: run(task)
    loop plan -> act -> observe
        AG->>LLM: messages + tool schemas
        LLM-->>AG: tool_calls
        AG->>AL: get_disrupted_flight / get_affected_passengers / search_alternative_flights
        AG->>KS: search_rebooking_policy x N (parallel)
    end
    AG->>SOLVE: optimize_rebooking(handles only)
    SOLVE->>SOLVE: compile retrieved policy-params -> constraints
    SOLVE-->>AG: allocation (source of truth)
    AG->>LLM: briefing from solver facts (citations verified)
    AG->>GW: propose_rebooking -> plan + approval PENDING
    GW-->>UI: WAITING_APPROVAL (SSE)
    Op->>UI: Approve (+ reviewed passengers)
    UI->>GW: POST /plans/{id}/approve (X-Operator-Id)
    GW->>AG: resume
    AG->>GW: authorize_execution
    GW-->>AG: signed single-use token bound to item digest
    AG->>AL: POST /api/bookings/rebookings + X-Approval-Token
    AL->>AL: verify signature, expiry, plan, item digest
    AL-->>AG: per-passenger results
    AG-->>UI: REPORT + COMPLETED (SSE)
```

## 4. Security boundaries

```mermaid
flowchart LR
    subgraph T[Technical boundary - NVIDIA OpenShell]
        direction TB
        A[Agent process<br/>user: sandbox] -->|allow GET /api/flights/**| AL[airline-service:8000]
        A -->|allow GET policies · POST optimization| RA[reroute-api:8000]
        A -->|allow POST /v1/chat/completions| N[integrate.api.nvidia.com:443]
        A -.->|DENY deny_rules POST /api/rebooking/plans/**| RA
        A -.->|DENY no policy| X[unknown-external-api.com]
        A -.->|DENY filesystem allow-list| K[~/.ssh/id_rsa]
    end
    subgraph B[Business boundary - Human approval]
        direction TB
        O([Operator]) -->|approve| G[Approval Gateway]
        G -->|HMAC token: plan, approval, item digest, exp| BK[Booking API]
        A2[Agent] -->|no token / wrong items / expired| BK
        BK -.->|401 / 403| A2
    end
```

- **OpenShell** answers *"can this process reach this endpoint / file?"* It is enforced by OpenShell in
  sandbox mode; in demo mode the same YAML is evaluated in-process and labelled "policy mirror".
- **Human approval** answers *"did an accountable person authorise this business change?"* It is
  enforced in the backend twice: the gateway only mints a token for an APPROVED, unexpired, unused
  approval, and the Booking API independently verifies that token against the exact item set.

The worker in remote mode has neither DB credentials nor the signing key; even a fully compromised agent
cannot change a booking without a human approval.

## 5. Human approval flow

```mermaid
stateDiagram-v2
    [*] --> PENDING: propose_rebooking (TTL 30 min)
    PENDING --> APPROVED: operator approves (human identity required, agent ids refused)
    PENDING --> REJECTED: operator rejects
    PENDING --> EXPIRED: TTL passed (checked lazily on every read)
    APPROVED --> Consumed: authorize_execution (single use, token exp 5 min)
    Consumed --> [*]: bookings re-issued, plan EXECUTED / PARTIALLY_EXECUTED
    REJECTED --> [*]
    EXPIRED --> [*]
```

Manual-review passengers (SSR, at-risk connections) are only executed if the operator explicitly
includes them in the approval; otherwise they are marked `HELD_FOR_OPERATOR`.

## 6. Optimization flow

```mermaid
flowchart LR
    P[Passengers<br/>tier · VIP · cabin · SSR · onward flight] --> F
    ALT[Alternative flights<br/>seats per cabin · carrier · times] --> F
    POL[Retrieved policies<br/>policy-params] --> C[Policy compiler<br/>PolicyRules + applied/missing] --> F
    W[config/optimization.yaml<br/>weights] --> F
    F[Formulation<br/>screen flights C5/C6<br/>eligible x p,f,c C4/C6<br/>costs C3 + objective] --> M[MilpProblem<br/>CSR matrix, bounds, binary vars]
    M -->|OPTIMIZATION_PROVIDER=cuopt| CU[NVIDIA cuOpt server]
    M -->|fallback, labelled| HI[HiGHS CPU]
    CU --> D[Decoder<br/>assignments · status · reasons · policy ids]
    HI --> D
    D --> R[OptimizationResult<br/>+ FCFS baseline comparison]
```

`x[p,f,c]` binary, `y[p]` binary; C1 `Σx + y = 1`, C2 capacity; C3 downgrade penalty (tier × VIP);
C4 MCT; C5 same destination; C6 carrier/window/flight-status policy; objective = delay × tier + VIP delay
+ downgrade + connection risk + rebooking cost + 100 000 × unassigned.
