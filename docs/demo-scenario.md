# Demo scenario — KE123 cancellation (≈ 3 minutes)

## Seed data (deterministic)

**KE123 ICN 10:00 → NRT 12:20, CANCELLED** (AOG – hydraulic system, airline fault). 35 passengers:

| Group | Count | Notes |
|---|---|---|
| Business | 7 | incl. 2 VIPs (P001 Kim Minjun, P002 Sarah Johnson) |
| Economy | 28 | incl. 1 VIP (P008) |
| Connections at NRT | 5 | JL006 19:00 (P002), DL180 18:30 (P009), **SQ637 17:45 (P010)**, **UA902 18:00 (P011)**, AC004 21:00 (P012) |
| Special assistance | 2 | P013 WCHC, P014 UMNR |

Alternatives found by the agent (all carriers, incl. co-terminal):

| Flight | Dep → Arr | J / Y seats | Outcome |
|---|---|---|---|
| KE701 | 14:00 → 16:20 | 2 / 10 | used |
| OZ102 | 15:30 → 17:50 | 3 / 8 | used (interline partner, IROP-002) |
| KE703 | 16:00 → 18:20 | 1 / 20 | used |
| 7C1102 | 13:30 → 15:50 | 0 / 15 | **excluded** — no interline agreement (IROP-002, C6) |
| KE2101 | 13:00 → HND 15:15 | 2 / 12 | **excluded** — co-terminal HND (IROP-005, C5) |
| KE125 | 19:30 → 21:50 | 4 / 24 | **excluded** — itself DELAYED (C6) |
| KE705 | +1d 09:00 → 11:20 | 8 / 30 | **excluded** — > 12 h window (IROP-003, C6) |

Expected solver result (identical for cuOpt and the CPU fallback — same MILP, optimum 115 367.5):

- **35 affected → 31 auto-assigned, 3 manual review, 1 no feasible**
- 1 business downgrade (6 business seats for 7 business passengers; goes to a non-VIP BASIC member, not a VIP)
- Manual review: P011 (UA902 margin 10 min < 30 min buffer, MCT-003), P013 WCHC and P014 UMNR (SSR-001)
- No feasible: P010 — KE701 arrives 85 min before SQ637 (< 90 min MCT-002); the only flight that would work is
  7C1102, blocked by IROP-002 → the agent flags "duty-manager ad-hoc endorsement, otherwise refund / re-routing (IROP-004)"
- vs. first-come-first-served desk process on the same eligible flights: missed connections 4 → 0,
  SSR violations 2 → 0, business downgrades 2 → 1, VIP average delay 5h40 → 4h00
  (average delay +6 min — an intended trade-off shown openly in the UI)

## Script

| Time | Action | What the judge sees |
|---|---|---|
| 0:00 | Open `http://localhost:3000` | Problem statement, 5 pillars with their NVIDIA component, runtime chips (green = NVIDIA live, amber = fallback) |
| 0:15 | **Run Agent** with the pre-filled Korean command | Stepper moves ①→③; timeline streams each tool call with its badge (Nemotron, Airline API, NeMo Retriever, cuOpt) and duration |
| 0:45 | Plan appears | KPIs 35/31/3/1, solver status and model size, operator briefing with verified policy citations |
| 1:05 | Scroll: *Why an optimizer?* + seat allocation | FCFS vs ReRoute table; per-flight loads never exceed capacity; excluded flights with the policy that excluded them |
| 1:25 | Allocation table → *Review* / *No feasible* tabs; hover a policy chip | Reasons + policy chips; the matching Policy Evidence card lights up (source document, score, compiled constraint) |
| 1:50 | **"승인 없이 execute API 직접 호출해보기"** | `HTTP 403 — APPROVAL_REQUIRED → blocked & audited` |
| 2:05 | Tick one manual-review passenger, **Approve Plan** | ⑤ Executing → Completed; Final Report 32 rebooked, 2 held, 1 alternative handling, VIP follow-ups, meal vouchers (IROP-004) |
| 2:30 | Security panel → **Run all probes** | ALLOW for airline / optimization / NIM; DENY for unknown host, self-approval (deny_rule), `~/.ssh/id_rsa`, secrets |
| 2:45 | Audit log → *Denied* filter | Every decision with timestamp, agent, tool, target, action, policy, result, enforced by |
| 2:55 | Scenario chip **KE125 45분 지연** → Run | Agent retrieves RBK-002 (180-min threshold) and ends with "no re-accommodation required" |

Reset between rehearsals with the **Reset** button (or `make reset`).

## Recording tips

- `AGENT_STEP_DELAY_MS=350` (default in compose) paces the timeline for video; set `0` for raw speed.
- 1600×1000 browser window matches the screenshots in `docs/screenshots/`.
- With a GPU: `OPTIMIZATION_PROVIDER=cuopt` + `docker compose --profile gpu up` — the solver chip turns green
  and the timeline shows the `cuOpt` badge; the numbers stay identical.
