---
name: reroute-irops
description: Operate the ReRoute airline disruption-recovery agent - start a recovery task for a cancelled or delayed flight, follow its tool calls, and summarise the proposed re-accommodation plan with its policy evidence for a human operator. Never approves or executes plans.
license: Apache-2.0
metadata:
  tags: [airline, irops, rebooking, cuopt, nemo-retriever, openshell]
---

# ReRoute IROPS operator skill

Use this skill when an operations user asks to handle a flight disruption (cancellation / delay) with ReRoute.
Base URL: `$REROUTE_API` (default `http://reroute-api:8000`).

## Workflow

1. Start a task with the user's instruction verbatim:
   `curl -s -X POST $REROUTE_API/api/agent/tasks -H 'content-type: application/json' -d '{"command":"<instruction>"}'`
   Keep the returned `id`.
2. Follow progress: `curl -s "$REROUTE_API/api/agent/tasks/<id>/events?stream=false"` until the task `state`
   (`GET /api/agent/tasks/<id>`) is `WAITING_APPROVAL`, `COMPLETED` or `FAILED`.
3. If `WAITING_APPROVAL`, fetch `GET /api/rebooking/plans/<plan_id>` and report:
   - affected / auto-assigned / manual review / no feasible counts (`summary`)
   - every non-auto passenger with its `reason` and `policy_ids`
   - the flights excluded by policy (`summary.excluded_flights`)
   - the solver used (`summary.provider`, `summary.nvidia`) - say "CPU fallback" if `nvidia` is false.
4. Tell the user the plan must be approved by an operator in the ReRoute console.

## Rules

- Never call `/api/rebooking/plans/*/approve`, `/reject` or `/execute`. Approval is a human decision
  (the sandbox policy denies these calls anyway).
- Quote policy IDs exactly as returned; never state airline rules from memory.
- Never change or "improve" the allocation - the solver output is the source of truth.
