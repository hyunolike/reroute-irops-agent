"""Check a deployed ReRoute MCP endpoint the way an external agent (e.g. OpenClaw) would use it.

    REROUTE_MCP_TOKEN=... python -m app.integrations.mcp_smoke https://reroute.example.com/mcp

Runs: list tools -> open task -> verify flight -> passengers -> alternatives -> policies (until coverage complete)
-> optimize -> explore exceptions -> propose, then prints the task id to approve in the dashboard.
It never approves anything - that is only possible for a human in the ReRoute console.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx2
from mcp import Client, types
from mcp.client.streamable_http import streamable_http_client


async def _tool(client: Client, name: str, **args):
    res = await client.call_tool(name, args)
    data = res.structured_content if res.structured_content is not None else json.loads(res.content[0].text)
    data = data.get("result", data) if isinstance(data, dict) and set(data) == {"result"} else data
    if isinstance(data, dict) and data.get("error"):
        print(f"  ! {name}: {data['error']}")
    return data


async def main(url: str, token: str, flight_no: str = "KE123") -> int:
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=120)
    async with Client(
        streamable_http_client(url, http_client=http),
        mode="legacy",
        client_info=types.Implementation(name="reroute-mcp-smoke", version="1.0"),
    ) as client:
        names = sorted(t.name for t in (await client.list_tools()).tools)
        print(f"tools ({len(names)}): {', '.join(names)}")
        task = await _tool(
            client, "open_recovery_task", instruction=f"{flight_no} is cancelled. Rebook the passengers optimally."
        )
        tid = task["task_id"]
        print(f"task {tid} - watch it live: {task['dashboard']}")
        await _tool(
            client, "log_reasoning", task_id=tid, note="MCP smoke test: verify, gather, ground in policy, optimize, propose."
        )
        flight = await _tool(client, "get_disrupted_flight", task_id=tid, flight_no=flight_no)
        print(f"flight: {flight.get('flight_no')} {flight.get('status')}")
        pax = await _tool(client, "get_affected_passengers", task_id=tid, flight_no=flight_no)
        print(f"passengers: {pax.get('count')}")
        await _tool(
            client,
            "search_alternative_flights",
            task_id=tid,
            origin=flight["origin"],
            destination=flight["destination"],
            departure_date=flight["departure_date"],
        )
        queries = ["minimum connection time", "interline partner carriers", "special assistance passengers"]
        for _ in range(4):
            pol = await _tool(client, "search_rebooking_policy", task_id=tid, queries=queries)
            missing = pol["coverage"]["missing"]
            print(f"policies grounded: {sorted(pol['coverage']['grounded'])} missing: {missing}")
            if not missing:
                break
            queries = pol["coverage"]["suggested_queries"]
        opt = await _tool(client, "optimize_rebooking", task_id=tid, flight_no=flight_no)
        print(
            f"solver: {opt.get('solver')} -> {json.dumps({k: opt['summary'][k] for k in ('auto_assigned', 'manual_review', 'no_feasible')})}"
        )
        for e in opt.get("exceptions", []):
            ex = await _tool(client, "explore_exception_options", task_id=tid, passenger_id=e["passenger_id"])
            print(f"  exception {e['passenger_id']}: {sum(o['feasible_under_policy'] for o in ex['options'])} feasible option(s)")
        prop = await _tool(client, "propose_rebooking", task_id=tid, flight_no=flight_no)
        status = await _tool(client, "get_recovery_status", task_id=tid)
        print(f"state: {status['state']} · approval: {status.get('plan', {}).get('approval')}")
        print(prop.get("next", ""))
        return 0 if status["state"] == "WAITING_APPROVAL" else 1


if __name__ == "__main__":
    if len(sys.argv) < 2 or not os.environ.get("REROUTE_MCP_TOKEN"):
        sys.exit("usage: REROUTE_MCP_TOKEN=... python -m app.integrations.mcp_smoke <https://host/mcp> [FLIGHT]")
    sys.exit(asyncio.run(main(sys.argv[1], os.environ["REROUTE_MCP_TOKEN"], *sys.argv[2:3])))
