"""Human approval is the business authorization boundary - enforced in the backend."""

from datetime import timedelta

from sqlalchemy import select

from app.db.base import utcnow
from app.db.models import Approval, Reservation
from tests.conftest import OPERATOR, run_agent_to_approval


async def test_plan_waits_for_approval_and_nothing_is_booked(client, container):
    task = await run_agent_to_approval(client, container)
    assert task["state"] == "WAITING_APPROVAL"
    plan = (await client.get(f"/api/rebooking/plans/{task['plan_id']}")).json()
    assert plan["status"] == "PENDING_APPROVAL" and plan["approval"]["status"] == "PENDING"
    with container.db.session() as s:
        assert not s.scalars(select(Reservation).where(Reservation.status == "REBOOKED")).all()


async def test_unauthorized_execution_is_blocked(client, container):
    task = await run_agent_to_approval(client, container)
    r = await client.post(f"/api/rebooking/plans/{task['plan_id']}/execute")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "APPROVAL_REQUIRED"
    denied = (await client.get("/api/audit", params={"result": "DENY"})).json()["entries"]
    assert any(e["action"] == "booking.execute" and e["enforced_by"] == "approval-gateway" for e in denied)


async def test_agent_tool_cannot_execute_without_approval(client, container):
    task = await run_agent_to_approval(client, container)
    from app.providers.llm.base import ToolCall
    from app.tools.base import AgentMemory, ToolContext

    ctx = ToolContext(
        task_id=task["id"], agent="reroute-agent", memory=AgentMemory(), http=container.http, gateway=container.gateway
    )
    out = await container.orchestrator._execute(task["id"], ToolCall("x", "execute_rebooking", {"plan_id": task["plan_id"]}), ctx)
    assert "APPROVAL_REQUIRED" in out


async def test_approval_requires_human_identity(client, container):
    task = await run_agent_to_approval(client, container)
    url = f"/api/rebooking/plans/{task['plan_id']}/approve"
    assert (await client.post(url, json={})).status_code == 401
    assert (await client.post(url, json={}, headers={"X-Operator-Id": "reroute-agent"})).status_code == 403


async def test_successful_rebooking_after_approval(client, container):
    task = await run_agent_to_approval(client, container)
    plan_id = task["plan_id"]
    plan = (await client.get(f"/api/rebooking/plans/{plan_id}")).json()
    manual = [i["id"] for i in plan["items"] if i["status"] == "MANUAL_REVIEW"][:1]
    r = await client.post(
        f"/api/rebooking/plans/{plan_id}/approve", json={"comment": "OK", "approved_manual_item_ids": manual}, headers=OPERATOR
    )
    assert r.status_code == 200 and r.json()["approval"]["status"] == "APPROVED"
    await container.runner.drain()

    task = (await client.get(f"/api/agent/tasks/{task['id']}")).json()
    assert task["state"] == "COMPLETED"
    assert task["report"]["rebooked"] == 32 and task["report"]["held_for_operator"] == 2 and task["report"]["no_feasible"] == 1
    plan = (await client.get(f"/api/rebooking/plans/{plan_id}")).json()
    assert plan["status"] == "EXECUTED"
    # Inventory really changed in the airline system
    ke701 = (await client.get("/api/flights/KE701")).json()
    assert ke701["business_available"] == 0 and ke701["economy_available"] < 10
    with container.db.session() as s:
        assert len(s.scalars(select(Reservation).where(Reservation.status == "REBOOKED")).all()) == 32
    # Approval is single-use
    assert (await client.post(f"/api/rebooking/plans/{plan_id}/execute")).status_code == 409
    audit = (await client.get("/api/audit", params={"task_id": task["id"]})).json()["entries"]
    assert any(e["action"] == "booking.rebook" and e["result"] == "SUCCESS" for e in audit)


async def test_reject_changes_nothing(client, container):
    task = await run_agent_to_approval(client, container)
    r = await client.post(f"/api/rebooking/plans/{task['plan_id']}/reject", json={"comment": "hold"}, headers=OPERATOR)
    assert r.json()["status"] == "REJECTED"
    assert (await client.get(f"/api/agent/tasks/{task['id']}")).json()["state"] == "REJECTED"
    assert (await client.post(f"/api/rebooking/plans/{task['plan_id']}/execute")).status_code == 403


async def test_expired_approval(client, container):
    task = await run_agent_to_approval(client, container)
    with container.db.session() as s:
        a = s.scalar(select(Approval).where(Approval.plan_id == task["plan_id"]))
        a.expires_at = utcnow() - timedelta(minutes=1)
        s.commit()
    r = await client.post(f"/api/rebooking/plans/{task['plan_id']}/approve", json={}, headers=OPERATOR)
    assert r.status_code == 409
    assert (await client.get(f"/api/rebooking/plans/{task['plan_id']}")).json()["approval"]["status"] == "EXPIRED"


async def test_token_bound_to_approved_items(client, container):
    """Even with a valid token, the booking API refuses a different item set (no swap after approval)."""
    task = await run_agent_to_approval(client, container)
    await client.post(f"/api/rebooking/plans/{task['plan_id']}/approve", json={}, headers=OPERATOR)
    await container.runner.drain()
    from app.approval.tokens import ApprovalClaims, items_digest, mint_token

    good = [{"reservation_id": "R0001", "flight_no": "KE701", "cabin": "BUSINESS"}]
    token = mint_token("test-secret", ApprovalClaims(task["plan_id"], "a", "ops", items_digest(good), 2**31))
    tampered = [{"reservation_id": "R0001", "flight_no": "KE703", "cabin": "BUSINESS"}]
    r = await client.post(
        "/api/bookings/rebookings", json={"plan_id": task["plan_id"], "items": tampered}, headers={"X-Approval-Token": token}
    )
    assert r.status_code == 403 and "differ" in r.text
