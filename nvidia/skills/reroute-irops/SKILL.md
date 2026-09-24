---
name: reroute-irops
description: Recover passengers of a cancelled or delayed flight with the ReRoute MCP tools - verify the flight, ground every rule in retrieved airline policy, let the solver allocate seats, analyse exceptions and request human approval. Never approves or executes plans.
license: Apache-2.0
metadata:
  tags: [airline, irops, rebooking, mcp, cuopt, nemo-retriever, openshell]
---

# ReRoute IROPS recovery (MCP)

Use when an operations user asks to handle a flight disruption. Tools come from the `reroute` MCP server.

## Workflow

1. `open_recovery_task(instruction)` with the user's words → keep `task_id`. Tell the user the dashboard link.
2. `log_reasoning(task_id, note)` with a 3-5 step plan. Add a short note before important steps.
3. `get_disrupted_flight(task_id, flight_no)`.
   - Operating normally → `finish_without_action`.
   - Delayed → `search_rebooking_policy(task_id, ["rebooking threshold for delayed flights"])`; if below the
     threshold → `finish_without_action(task_id, reason citing the policy id)`.
4. `get_affected_passengers`, then `search_alternative_flights(origin, destination, departure_date)`.
5. `search_rebooking_policy(task_id, queries)` - repeat with `coverage.suggested_queries` until `coverage.missing` is empty.
6. `optimize_rebooking(task_id, flight_no)` - the allocation is final.
7. For each passenger in `exceptions`: `explore_exception_options(task_id, passenger_id)`.
8. `propose_rebooking(task_id, flight_no)`.
9. Report to the user: counts (auto / review / no feasible), each exception with its policy ids and a recommended
   operator action, and that approval happens in the ReRoute console.

If a tool returns `error`, read `next_step_hint`, fix the call and continue.

## Rules

- Airline rules come only from `search_rebooking_policy`; quote policy ids exactly.
- Never change, re-order or "improve" the solver allocation.
- You cannot approve, reject or execute a plan - only a human operator can, in the ReRoute console.
