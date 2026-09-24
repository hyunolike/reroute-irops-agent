"""ReRoute tools as an MCP server (Streamable HTTP) for external agents such as OpenClaw inside NVIDIA NemoClaw.

    nemoclaw <sandbox> mcp add reroute --url https://<host>/mcp --env REROUTE_MCP_TOKEN

The external agent is the planner ("brain"). Every tool call it makes goes through ReRoute's orchestrator with the
same schema validation, preconditions, state machine, event log (visible live on the dashboard) and audit trail as
ReRoute's own agent. There is deliberately NO tool to approve, reject or execute a plan: booking changes remain a
human decision in the ReRoute operations console.
"""

from __future__ import annotations

import hmac
import json
from datetime import date
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from app.container import Container

INSTRUCTIONS = """ReRoute - airline disruption (IROPS) recovery tools.
Workflow: open_recovery_task(instruction) -> get_disrupted_flight -> get_affected_passengers ->
search_alternative_flights -> search_rebooking_policy (until coverage.missing is empty) -> optimize_rebooking ->
explore_exception_options for each exception passenger -> propose_rebooking.
If the flight needs no re-accommodation, call finish_without_action with the policy id you relied on.
Use log_reasoning to show the operator why you take each step. Airline rules must come from
search_rebooking_policy, never from memory. The allocation is computed by the solver and cannot be changed.
You cannot approve or execute a plan: a human operator approves in the ReRoute console."""


def _client_name(ctx: Context | None) -> str:
    try:
        info = ctx.session.client_params.client_info  # type: ignore[union-attr]
        if info and info.name:
            return f"{info.name} (MCP)"
    except Exception:  # noqa: BLE001 - stateless requests may not carry client params
        pass
    try:
        ua = (ctx.headers or {}).get("user-agent")  # type: ignore[union-attr]
        if ua:
            return f"{ua.split('/')[0][:40]} (MCP)"
    except Exception:  # noqa: BLE001
        pass
    return "mcp-client"


def build_mcp_server(c: Container) -> MCPServer:
    server = MCPServer(name="reroute", title="ReRoute IROPS recovery tools", instructions=INSTRUCTIONS, version="0.2.0")
    orch = c.orchestrator
    dashboard = c.settings.public_url.rstrip("/")

    def audit(ctx: Context | None, tool: str, target: str, result: str, details: dict[str, Any] | None = None) -> None:
        c.audit.record(
            agent=_client_name(ctx),
            tool=f"mcp.{tool}",
            target=target,
            action="mcp.tool_call",
            policy="mcp-bearer",
            result=result,
            enforced_by="reroute-mcp",
            task_id=target if len(target) == 12 else None,
            details=details or {},
        )

    async def call(ctx: Context | None, task_id: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            out = await orch.external_call(task_id, tool, args)
        except (KeyError, PermissionError) as e:
            out = {"error": str(e).strip('"')}
        audit(ctx, tool, task_id, "FAILURE" if "error" in out else "SUCCESS", {"args": args})
        return out

    @server.tool()
    async def open_recovery_task(instruction: str, ctx: Context) -> dict[str, Any]:
        """Start a recovery task for an operator instruction (e.g. "KE123 was cancelled, rebook the passengers").
        Returns the task_id every other tool needs. The task appears live on the ReRoute dashboard."""
        task_id = orch.open_external_task(instruction, _client_name(ctx), c.runtime_info())
        audit(ctx, "open_recovery_task", task_id, "SUCCESS", {"instruction": instruction})
        return {"task_id": task_id, "dashboard": dashboard, "next": "call get_disrupted_flight with the flight number"}

    @server.tool()
    async def log_reasoning(task_id: str, note: str, ctx: Context) -> dict[str, Any]:
        """Show a short note on the operator dashboard explaining what you learned and why you take the next step."""
        try:
            orch.external_note(task_id, _client_name(ctx), note)
            return {"ok": True}
        except (KeyError, PermissionError) as e:
            return {"error": str(e).strip('"')}

    @server.tool()
    async def get_disrupted_flight(task_id: str, flight_no: str, ctx: Context) -> dict[str, Any]:
        """Verify a flight's operational status (SCHEDULED / DELAYED / CANCELLED), schedule and disruption. Call first."""
        return await call(ctx, task_id, "get_disrupted_flight", {"flight_no": flight_no})

    @server.tool()
    async def get_affected_passengers(task_id: str, flight_no: str, ctx: Context) -> dict[str, Any]:
        """Load the passenger manifest summary of the disrupted flight (counts, VIPs, connections, special assistance)."""
        return await call(ctx, task_id, "get_affected_passengers", {"flight_no": flight_no})

    @server.tool()
    async def search_alternative_flights(
        task_id: str, origin: str, destination: str, departure_date: date, ctx: Context
    ) -> dict[str, Any]:
        """Search alternative flights (all carriers, incl. co-terminal airports) with seats left per cabin."""
        return await call(
            ctx,
            task_id,
            "search_alternative_flights",
            {"origin": origin, "destination": destination, "departure_date": departure_date.isoformat()},
        )

    @server.tool()
    async def search_rebooking_policy(task_id: str, queries: list[str], ctx: Context, top_k: int = 3) -> dict[str, Any]:
        """Search airline rebooking / fare / VIP / connection / special-assistance / IROPS policies. Returns policy ids,
        text, scores and `coverage` (rules still missing + suggested queries). Keep searching until coverage.missing is empty."""
        return await call(ctx, task_id, "search_rebooking_policy", {"queries": queries, "top_k": top_k})

    @server.tool()
    async def optimize_rebooking(task_id: str, flight_no: str, ctx: Context) -> dict[str, Any]:
        """Compute the optimal allocation with the MILP solver (NVIDIA cuOpt). The result is final; you cannot change it."""
        return await call(ctx, task_id, "optimize_rebooking", {"flight_no": flight_no})

    @server.tool()
    async def explore_exception_options(task_id: str, passenger_id: str, ctx: Context) -> dict[str, Any]:
        """For a MANUAL_REVIEW / NO_FEASIBLE passenger: every option with the exact blocking constraint and policy id."""
        return await call(ctx, task_id, "explore_exception_options", {"passenger_id": passenger_id})

    @server.tool()
    async def propose_rebooking(task_id: str, flight_no: str, ctx: Context) -> dict[str, Any]:
        """Package the solver allocation with policy evidence into a plan and request HUMAN approval (no booking change)."""
        return await call(ctx, task_id, "propose_rebooking", {"flight_no": flight_no})

    @server.tool()
    async def finish_without_action(task_id: str, reason: str, ctx: Context) -> dict[str, Any]:
        """Close the task when no re-accommodation is needed (e.g. delay below the policy threshold). Refused unless the
        retrieved facts and policies justify it."""
        try:
            out = orch.external_finish_without_action(task_id, reason)
        except (KeyError, PermissionError) as e:
            out = {"error": str(e).strip('"')}
        audit(ctx, "finish_without_action", task_id, "FAILURE" if "error" in out else "SUCCESS", {"reason": reason})
        return out

    @server.tool()
    async def delegate_recovery(instruction: str, ctx: Context) -> dict[str, Any]:
        """Hand the whole task to ReRoute's own recovery agent (Nemotron) instead of planning it yourself.
        Poll get_recovery_status with the returned task_id."""
        task = c.repo.create_task(instruction, runtime={**c.runtime_info(), "delegated_by": _client_name(ctx)})
        if c.settings.agent_execution == "remote":
            c.repo.update_task(task.id, pending="run")
        else:
            c.runner.submit(orch.run(task.id))
        audit(ctx, "delegate_recovery", task.id, "SUCCESS", {"instruction": instruction})
        return {"task_id": task.id, "dashboard": dashboard, "next": "poll get_recovery_status"}

    @server.tool()
    async def get_recovery_status(task_id: str) -> dict[str, Any]:
        """Task state, plan summary, exception passengers and approval status (read-only)."""
        t = c.repo.get_task(task_id)
        if t is None:
            return {"error": f"unknown task {task_id}"}
        out: dict[str, Any] = {"task_id": t.id, "state": t.state, "error": t.error, "report": t.report}
        if t.plan_id:
            p = c.gateway.get_plan(t.plan_id)
            a = c.gateway.get_approval(t.plan_id)
            out["plan"] = {
                "plan_id": p.id,
                "status": p.status,
                "solver": p.solver,
                "summary": {k: p.summary.get(k) for k in ("affected", "auto_assigned", "manual_review", "no_feasible")},
                "exceptions": [
                    {"passenger": i.passenger_name, "status": i.status, "reason": i.reason, "policies": i.policy_ids}
                    for i in p.items
                    if i.status not in ("AUTO_ASSIGNED", "EXECUTED")
                ],
                "approval": a.status if a else None,
            }
        return json.loads(json.dumps(out, default=str))

    return server


class BearerGuard:
    """Requires `Authorization: Bearer <REROUTE_MCP_TOKEN>` (what `nemoclaw mcp add --env` injects)."""

    def __init__(self, app: ASGIApp, token: str, container: Container) -> None:
        self.app, self.token, self.c = app, token, container

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            supplied = headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not supplied or not hmac.compare_digest(supplied, self.token):
                self.c.audit.record(
                    agent=headers.get("user-agent", "unknown")[:60],
                    tool="mcp",
                    target=scope.get("path", "/mcp"),
                    action="mcp.connect",
                    policy="mcp-bearer",
                    result="DENY",
                    enforced_by="reroute-mcp",
                    details={"reason": "missing or invalid bearer token"},
                )
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def mount_mcp(app, c: Container) -> MCPServer | None:
    """Adds POST/GET /mcp to the FastAPI app when REROUTE_MCP_TOKEN is configured (disabled otherwise)."""
    if c.settings.mcp_bearer_token is None:
        return None
    server = build_mcp_server(c)
    hosts = [h.strip() for h in c.settings.mcp_allowed_hosts.split(",") if h.strip()]
    starlette_app = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=bool(hosts), allowed_hosts=hosts),
    )
    endpoint = starlette_app.routes[0].endpoint  # type: ignore[attr-defined]
    guarded = BearerGuard(endpoint, c.settings.mcp_bearer_token.get_secret_value(), c)
    app.router.routes.append(Route("/mcp", endpoint=guarded, methods=["GET", "POST", "DELETE"]))
    return server
