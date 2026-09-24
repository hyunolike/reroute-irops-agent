# The ReRoute agent: structure and behaviour

[🇰🇷 한국어](agent.md) · **🇺🇸 English**

This document shows **what a single agent is made of and how it handles one request**.
For the overall system and deployment see [architecture.en.md](architecture.en.md).

| Diagram | Shows |
|---|---|
| [1. Agent structure](#1-agent-structure) | the parts: brain, hands, memory, safety |
| [2. Agent loop](#2-agent-loop-plan--act--observe) | what happens every turn: plan → act → observe |
| [3. One KE123 run](#3-one-ke123-run) | a real request, step by step |
| [4. Who decides what](#4-who-decides-what) | LLM · code · solver · human · OpenShell |
| [5. What the LLM sees and does not see](#5-what-the-llm-sees-and-does-not-see) | data never passes through the LLM |
| [6. Recovery paths](#6-recovery-paths) | when the model is wrong or stops |
| [7. Two execution modes](#7-two-execution-modes) | inside the API vs sandboxed worker |
| [8. External agent mode](#8-external-agent-mode-openclaw--mcp) | OpenClaw in NemoClaw uses ReRoute over MCP |

---

## 1. Agent structure

```mermaid
flowchart TB
    goal(["Operator goal in one sentence<br/>“KE123 is cancelled…”"]) --> orch

    subgraph agent["ReRoute agent"]
        direction TB
        orch["🧭 Orchestrator<br/>agent loop · state machine · guardrails<br/>(max 30 turns, up to 2 guidance nudges)"]

        subgraph brain["🧠 Brain — planning and tool choice"]
            nim["Nemotron (NIM)<br/>function calling"]
            fb["Scripted planner<br/>(only without a key; warning in UI)"]
        end

        subgraph hands["🛠 Hands — 8 tools (typed arguments)"]
            t1["Lookup<br/>get_disrupted_flight<br/>get_affected_passengers<br/>search_alternative_flights"]
            t2["Knowledge<br/>search_rebooking_policy"]
            t3["Delegate decision<br/>optimize_rebooking"]
            t4["Exception analysis<br/>explore_exception_options"]
            t5["Propose · execute<br/>propose_rebooking<br/>execute_rebooking 🔒"]
        end

        mem[("📒 Working memory<br/>flight · passengers · alternatives<br/>retrieved policies · optimization result<br/>exception analyses · plan id")]
        guard["🛡 Governed egress<br/>every call: policy check → audit"]
        log["📡 Event log<br/>→ dashboard live (SSE)"]
    end

    orch <--> brain
    orch --> hands
    hands <--> mem
    hands --> guard
    orch --> log

    guard --> svc1["Mock airline API"]
    guard --> svc2["Knowledge service → NeMo Retriever"]
    guard --> svc3["Optimization service → cuOpt"]
    guard --> svc4["Approval gateway"]
```

- **Brain:** picks what to do next. In real mode Nemotron chooses the tool and its arguments every turn.
- **Hands:** the 8 tools. Every domain action goes through them to an HTTP API; the agent never touches the database.
- **Memory:** large data (35 passengers) lives here; the LLM only sees summaries (diagram 5).
- **Safety:** the orchestrator checks order, arguments and preconditions; every outbound call is checked against the OpenShell policy and audited.

## 2. Agent loop (plan → act → observe)

```mermaid
flowchart TD
    start(["Goal received<br/>RECEIVED"]) --> think

    think["🧠 Send to the LLM<br/>system rules + conversation + 8 tool schemas"] --> resp{"Does the reply<br/>contain tool calls?"}

    resp -- "yes (several allowed)" --> v1{"Registered tool?"}
    v1 -- no --> err["Return the error to the LLM<br/>(guardrail event logged)"]
    v1 -- yes --> v2{"Arguments valid?<br/>(Pydantic)"}
    v2 -- no --> err
    v2 -- yes --> v3{"Preconditions met?<br/>e.g. policies before optimizing"}
    v3 -- no --> err
    v3 -- yes --> act["🛠 Run the tool<br/>state change → policy check → HTTP call"]
    act --> obs["👀 Observe<br/>add result summary (+ policy coverage)<br/>to the conversation · update memory"]
    err --> think
    obs --> done{"Plan proposed?"}
    done -- no --> think
    done -- yes --> wait(["Waiting for operator approval<br/>WAITING_APPROVAL"])

    resp -- no --> stop{"Do the facts<br/>allow stopping?"}
    stop -- "yes: operating normally / delay below policy threshold" --> noact(["Completed, no action<br/>COMPLETED"])
    stop -- "flight cannot be verified" --> fail(["Failure reported<br/>FAILED"])
    stop -- no --> nudge{"Nudges < 2?"}
    nudge -- yes --> hint["Concrete next step<br/>e.g. “call optimize_rebooking(KE123)”"] --> think
    nudge -- no --> takeover["Scripted planner takes over<br/>(logged)"] --> think
```

## 3. One KE123 run

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operator
    participant O as Orchestrator
    participant L as Nemotron
    participant T as Tools
    participant M as Working memory
    participant S as Services<br/>(airline · RAG · cuOpt · approval)

    Op->>O: "KE123 is cancelled. Build the optimal re-accommodation plan."
    O->>L: rules + goal + tool schemas
    L-->>O: plan steps 1-5 + get_disrupted_flight(KE123)
    O->>T: validate, then run
    T->>S: GET /api/flights/KE123
    S-->>T: CANCELLED, technical issue
    T->>M: store flight
    T-->>O: summary
    O->>L: observation appended
    L-->>O: get_affected_passengers, search_alternative_flights
    O->>T: run (35 passengers · 7 alternatives → memory)
    O->>L: summary only (35 pax, 3 VIP, 5 connections, 2 SSR)
    L-->>O: search_rebooking_policy(several queries)
    T->>S: policy search (NeMo Retriever)
    T-->>O: policies + coverage {missing: none}
    L-->>O: optimize_rebooking(KE123)
    T->>S: memory data + policies → MILP → cuOpt
    S-->>T: allocation (31 auto · 3 review · 1 no feasible)
    L-->>O: explore_exception_options(P010, P011, P013, P014)
    T-->>O: P010: 7C1102 would save the connection but is blocked by IROP-002
    L-->>O: propose_rebooking(KE123)
    O->>L: write operator briefing from solver facts (citations verified)
    T->>S: store plan + request approval (PENDING)
    O-->>Op: WAITING_APPROVAL (live on the dashboard)
    Note over Op,S: only after approval: execute_rebooking → gateway token → Booking API
```

The model decides how many tools to call per turn and how often to search, so turn structure varies between runs.
What does not vary: the validation rules, the solver result, and the approval boundary.

## 4. Who decides what

```mermaid
flowchart TB
    subgraph llm["🧠 Nemotron decides"]
        l1["which tool to call next"]
        l2["search wording · whether to search more"]
        l3["which exception passengers to analyse"]
        l4["the operator briefing text"]
    end
    subgraph code["🧭 Orchestrator code enforces"]
        c1["registered tools · argument schema · preconditions"]
        c2["state machine · turn budget"]
        c3["briefing citations verified"]
    end
    subgraph solver["🧮 cuOpt solver decides"]
        s1["who goes on which flight and cabin"]
    end
    subgraph rag["📚 Retrieved policies decide"]
        r1["constraint values: MCT · partners · time window"]
    end
    subgraph human["👤 Human decides"]
        h1["approve / reject"]
        h2["whether review passengers are included"]
    end
    subgraph shell["🛡 OpenShell decides"]
        o1["reachable hosts · paths · files"]
    end

    llm -->|"proposes"| code
    code -->|"validated calls"| solver
    rag -->|"constraints"| solver
    solver -->|"allocation"| human
    shell -.->|"wraps all traffic"| code
```

The LLM decides **how to work**. It does not decide **outcomes (allocation) or permissions (approval, access)**.

## 5. What the LLM sees and does not see

```mermaid
flowchart LR
    subgraph seen["Visible to the LLM"]
        a1["goal sentence"]
        a2["tool schemas"]
        a3["result summaries<br/>e.g. 35 pax · 3 VIP · 5 connections"]
        a4["policy excerpts + coverage"]
        a5["optimization summary · exception list"]
    end
    subgraph hidden["Never passes through the LLM"]
        b1["full data of 35 passengers"]
        b2["seat inventory"]
        b3["MILP: 148 variables · 41 constraints"]
        b4["raw allocation"]
        b5["approval signing key · DB credentials"]
    end
    seen -->|"tool arguments are handles only<br/>(flight no, query, passenger id)"| tools["Tools"]
    tools <--> hidden
```

So there is structurally no path for the LLM to "adjust" the allocation or leak passenger data.

## 6. Recovery paths

```mermaid
flowchart TD
    x1["Wrong order<br/>e.g. optimize before policies"] --> g1["Precondition error returned<br/>+ what is missing"] --> ok(["Model corrects itself"])
    x2["Argument shape error<br/>e.g. a single query string"] --> g2["Harmless variants normalised<br/>otherwise validation error"] --> ok
    x3["Tool call written as text<br/>#lt;TOOLCALL#gt; …"] --> g3["Tool call parsed from text"] --> ok
    x4["Stops early"] --> g4["Concrete next-step guidance<br/>(up to 2×)"] --> ok
    x4 --> g5["If it keeps stopping, the scripted planner takes over<br/>(logged as GUARDRAIL)"]
    x5["Transient NIM error 429/5xx"] --> g6["Retry with exponential backoff"] --> ok
    x6["Persistent NIM outage"] --> g5
    x7["Call to a non-allowed host"] --> g7["Blocked by OpenShell policy + audited"]
    x8["Execute without approval"] --> g8["Approval gateway 403 + audited"]
```

## 7. Two execution modes

```mermaid
flowchart LR
    subgraph inline["inline mode (default demo)"]
        api1["reroute-api process"] --- ag1["Agent<br/>(same process)"]
        ag1 --- db1[("uses the DB directly")]
    end
    subgraph remote["remote mode (secured operation)"]
        api2["reroute-api<br/>(control plane)"]
        subgraph sb["OpenShell sandbox"]
            ag2["Agent worker<br/>no DB credentials<br/>no signing key"]
        end
        ag2 -->|"claim tasks · events · plans<br/>/internal/agent/*"| api2
        ag2 -->|"execution token only<br/>after approval"| api2
        api2 --- db2[("DB")]
    end
```

With `AGENT_EXECUTION=remote` the agent is a separate process inside the sandbox. Even a compromised agent cannot change
bookings without a human approval.

## 8. External agent mode (OpenClaw → MCP)

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operator
    participant OC as OpenClaw<br/>(NemoClaw sandbox)
    participant P as OpenShell proxy
    participant MCP as ReRoute /mcp
    participant O as Orchestrator
    participant D as Dashboard

    Op->>OC: "KE123 is cancelled, build the rebooking plan"
    OC->>P: tools/call open_recovery_task
    P->>P: protocol: mcp policy check · bearer token injected
    P->>MCP: forward
    MCP->>O: open external-planner session
    O-->>D: "task started by external agent"
    loop OpenClaw plans
        OC->>MCP: get_disrupted_flight / search_rebooking_policy / optimize_rebooking …
        MCP->>O: same validation · preconditions · state machine
        O-->>MCP: result or error + next_step_hint
        MCP-->>OC: observation
    end
    OC->>MCP: propose_rebooking
    O-->>D: waiting for approval (PENDING)
    Note over OC,MCP: no approve / execute tool exists over MCP
    Op->>D: approve
    D->>O: execute → approval-gateway token → Booking API
```

Even with an external brain, ReRoute's rules hold: no optimization without policy coverage, no "no action" without the facts,
and only humans approve.

---

Code: `apps/api/app/agent/orchestrator.py` (loop · guardrails) · `app/agent/prompts.py` (rules) · `app/tools/` (8 tools) ·
`app/providers/llm/` (Nemotron adapter · selection) · `app/agent/worker.py` (sandbox worker) · `app/agent/evaluate.py` (real-model evaluation) · `app/integrations/mcp_server.py` (MCP server)
